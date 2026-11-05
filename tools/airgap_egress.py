"""The egress report of tools/airgap-rehearsal.sh: what tried to leave the gap.

Reads the evidence the rehearsal collects in its report directory and
says, for each attempt to reach something outside the cluster, when, to
where, and from which pod or node process:

    dropped.txt     tcpdump of NFLOG group 5: every packet the gap's
                    netfilter rules dropped (they log, then drop)
    attempts.txt    tcpdump of every interface: connection attempts and
                    DNS packets, for the ones that never reached a rule
    coredns.log     CoreDNS's `log` plugin, with kubectl's timestamps: every
                    name any pod asked for, and the pod that asked
    events.json     `kubectl get events -A`: every image pull
    node-images.json  `crictl images`: what the node holds
    pods.json       `kubectl get pods -A`: who had which address
    phases.txt      epoch and name of each phase (gap-closed, probes-done, end)

Attempts between `gap-closed` and `probes-done` are the rehearsal's own
probes (curl, crictl pull, a pod's socket) and are listed as such.
CoreDNS's checks on its own upstream (no workload's question) are listed
as `cluster DNS`. Any other attempt after `probes-done` is a finding, and
the exit status is 1.

Standard library only; run by the rehearsal with the host's python3.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import sys

# 2026-11-03 15:22:01.123456 [iface In|Out] IP 10.244.0.3.45678 > 1.1.1.1.443: Flags [S], ...
PACKET = re.compile(
    r'^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\s+(?:(?P<iface>\S+)\s+(?:In|Out|M|B|P)\s+)?'
    r'(?P<proto>IP6?) (?P<src>[0-9a-f:.]+?)(?:\.(?P<sport>\d+))? > '
    r'(?P<dst>[0-9a-f:.]+?)(?:\.(?P<dport>\d+))?: (?P<rest>.*)$'
)
QUESTION = re.compile(r'\b(A|AAAA|PTR|SRV|MX|TXT|CNAME|HTTPS|SVCB|NS|SOA|HINFO)\? (\S+)')
# [pod/coredns-…/coredns] 2026-11-03T15:22:01.123456789Z [INFO] 10.244.0.5:36459 - 6164
#     "A IN example.com. udp 29 false 512" NXDOMAIN …
COREDNS = re.compile(
    r'(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z) \[INFO\] (?P<client>[0-9a-f:.\[\]]+):\d+ - \d+ '
    r'"(?P<type>\S+) IN (?P<name>\S+) (?P<proto>udp|tcp) [^"]*" (?P<rcode>\S+)'
)


def epoch_packet(ts: str) -> float:
    return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp()


def epoch_rfc3339(ts: str) -> float:
    whole, _, frac = ts.rstrip('Z').partition('.')
    base = datetime.strptime(whole, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
    return base + (float(f'0.{frac}') if frac else 0.0)


class Evidence:
    def __init__(self, report: Path, node_ip: str, gateway: str, allow: list[str], registry: str) -> None:
        self.report = report
        self.node_ip = node_ip
        self.gateway = ipaddress.ip_address(gateway)
        self.allow = [ipaddress.ip_network(n) for n in allow]
        self.registry = registry
        self.phases = self._phases()
        self.who = self._addresses()

    def _phases(self) -> dict[str, float]:
        phases = {}
        for line in (self.report / 'phases.txt').read_text().splitlines():
            when, _, name = line.partition(' ')
            phases[name.strip()] = float(when)
        return phases

    def _addresses(self) -> dict[str, str]:
        who = {self.node_ip: 'the node (kubelet, containerd, or a hostNetwork pod)'}
        pods = json.loads((self.report / 'pods.json').read_text())
        for pod in pods['items']:
            ip = pod.get('status', {}).get('podIP')
            if ip and not pod['spec'].get('hostNetwork'):
                who[ip] = f'pod {pod["metadata"]["namespace"]}/{pod["metadata"]["name"]}'
        return who

    def source(self, ip: str) -> str:
        if ip in self.who:
            return self.who[ip]
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if address.is_loopback:
            return 'a process on the node (loopback)'
        if address in ipaddress.ip_network('10.244.0.0/16'):
            return f'pod at {ip} (gone by the time of the report)'
        return ip

    def outside(self, ip: str) -> bool:
        address = ipaddress.ip_address(ip)
        if str(address) == '127.0.0.11' or address == self.gateway:
            return True  # Docker's DNS and the host: both outside the gap
        if address.is_link_local or address.is_multicast:
            return False
        return not any(address in net for net in self.allow)

    def phase(self, when: float) -> str:
        if when < self.phases.get('gap-closed', 0):
            return 'before the gap'
        if when < self.phases.get('probes-done', float('inf')):
            return 'probe'
        return 'install/journey'


def packets(path: Path):
    if not path.exists():
        return
    for line in path.read_text(errors='replace').splitlines():
        m = PACKET.match(line)
        if m:
            yield m, line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--node-ip', required=True)
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--allow', nargs='+', required=True)
    parser.add_argument('--registry', required=True)
    args = parser.parse_args()
    ev = Evidence(args.report, args.node_ip, args.gateway, args.allow, args.registry)

    # (phase, source, destination, what) -> [count, first line]
    attempts: dict[tuple[str, str, str, str], list] = {}

    def record(phase: str, src: str, dst: str, what: str, line: str) -> None:
        key = (phase, src, dst, what)
        if key in attempts:
            attempts[key][0] += 1
        else:
            attempts[key] = [1, line]

    for name in ('dropped.txt', 'attempts.txt'):
        for m, line in packets(args.report / name):
            dst = m['dst']
            try:
                if not ev.outside(dst):
                    continue
            except ValueError:
                continue
            q = QUESTION.search(m['rest'])
            if q:
                what = f'DNS {q[1]}? {q[2]}'
            elif m['dport']:
                what = f'{"tcp" if "Flags [" in m["rest"] else "udp"}/{m["dport"]}'
            else:
                what = m['rest'].split(',')[0][:40]
            source = ev.source(m['src'])
            phase = ev.phase(epoch_packet(m['ts']))
            # CoreDNS's own traffic to its upstream: `forward`'s health
            # check (NS for the root, every half second while the upstream
            # is down) and `loop`'s start-up probe (HINFO for a random
            # name). No workload asked for anything; names a workload asks
            # for are in coredns.log, below. Listed, not counted.
            if source.startswith('pod kube-system/coredns') and q and ((q[1], q[2]) == ('NS', '.') or q[1] == 'HINFO'):
                phase = 'cluster DNS'
            record(phase, source, dst, what, line)

    # Names CoreDNS was asked for that are not the cluster's own: it
    # forwards those upstream, which is out of the gap.
    asked: dict[tuple[str, str, str], list] = {}
    coredns = args.report / 'coredns.log'
    if coredns.exists():
        for line in coredns.read_text(errors='replace').splitlines():
            m = COREDNS.search(line)
            if not m:
                continue
            name = m['name']
            if name.endswith('.cluster.local.') or name == 'cluster.local.':
                continue
            if name.endswith('.in-addr.arpa.'):
                octets = name[: -len('.in-addr.arpa.')].split('.')
                if len(octets) == 4:
                    try:
                        if not ev.outside('.'.join(reversed(octets))):
                            continue
                    except ValueError:
                        pass
            client = m['client'].strip('[]')
            if m['type'] == 'HINFO' and ipaddress.ip_address(client).is_loopback:
                continue  # CoreDNS's `loop` plugin asking itself
            key = (ev.phase(epoch_rfc3339(m['ts'])), ev.source(client), f'{m["type"]} {name} -> {m["rcode"]}')
            if key in asked:
                asked[key][0] += 1
            else:
                asked[key] = [1, line]

    # Image pulls: every one should name the gap's registry.
    pulls: dict[str, set[str]] = defaultdict(set)
    events = json.loads((args.report / 'events.json').read_text())
    for e in events['items']:
        m = re.search(r'(?:Pulling|Successfully pulled|Failed to pull) image "([^"]+)"', e.get('message', ''))
        if m:
            obj = e.get('involvedObject', {})
            pulls[m[1]].add(f'{obj.get("namespace")}/{obj.get("name")}')
    foreign_pulls = {ref: who for ref, who in pulls.items() if not ref.startswith(args.registry + '/')}
    node_images = json.loads((args.report / 'node-images.json').read_text())['images']
    held = sorted({tag for image in node_images for tag in image.get('repoTags') or image.get('repoDigests') or []})

    out: list[str] = ['# Egress report', '']
    out.append(f'Phases (epoch): {json.dumps(ev.phases)}')
    out.append('')
    out.append('## Image pulls the cluster made')
    out.append('')
    for ref, who in sorted(pulls.items()):
        flag = '' if ref not in foreign_pulls else '  **NOT FROM THE GAP REGISTRY**'
        out.append(f'- `{ref}` for {", ".join(sorted(who))}{flag}')
    if not pulls:
        out.append('- none recorded')
    out.append('')
    out.append('## Images on the node (kind preloads its own system images)')
    out.append('')
    out += [f'- `{ref}`' for ref in held]
    out.append('')

    findings = 0
    for title, rows, phase_of in (
        ('Packets to outside the cluster', attempts, lambda k: k[0]),
        ('DNS names CoreDNS was asked to resolve outside the cluster', asked, lambda k: k[0]),
    ):
        for phase in ('before the gap', 'probe', 'cluster DNS', 'install/journey'):
            if phase == 'cluster DNS' and rows is asked:
                continue
            selected = {k: v for k, v in rows.items() if phase_of(k) == phase}
            out.append(f'## {title}: {phase}')
            out.append('')
            if not selected:
                out.append('- none')
            for key, (count, line) in sorted(selected.items()):
                out.append(f'- {" | ".join(key[1:])} (x{count})')
                out.append(f'  - `{line.strip()[:220]}`')
            out.append('')
            if phase == 'install/journey':
                findings += len(selected)
    if foreign_pulls:
        findings += len(foreign_pulls)

    out.append(
        f'**{findings} finding(s)** after the probes.'
        if findings
        else '**No attempt to leave the cluster** during the install and the journey.'
    )
    text = '\n'.join(out) + '\n'
    (args.report / 'egress.md').write_text(text)
    print(text)
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
