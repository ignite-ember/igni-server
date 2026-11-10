from fastapi import APIRouter, Depends

from .endpoints.client import (
    auth,
    auth_exchange,
    auth_methods,
    chat,
    nodes,
    notifications,
    oauth,
    oidc,
)
from .endpoints.cloud import (
    app_vm_profile as cloud_app_vm_profile,
    mcp_approvals as cloud_mcp_approvals,
    my_events as cloud_my_events,
    spaces as cloud_spaces,
    vm_commands as cloud_vm_commands,
)
from .endpoints.identity import (
    clients as identity_clients,
    directory as identity_directory,
    extensions as identity_extensions,
    revocations as identity_revocations,
    service_events as identity_events,
    service_notifications as identity_notifications,
    service_vm_profile as identity_vm_profile,
    token as identity_token,
)
from .endpoints.platform import (
    producer_events as platform_producer_events,
    producer_exports as platform_producer_exports,
    producers as platform_producers,
    vm_commands as platform_vm_commands,
    vm_credentials as platform_vm_credentials,
    vm_resets as platform_vm_resets,
)
from .endpoints.portal import (
    access_review,
    api_keys as portal_api_keys,
    apps as portal_apps,
    audit,
    authorize as portal_authorize,
    clients as portal_clients,
    deployment as portal_deployment,
    erasure,
    extension_priority,
    group_entries,
    group_extensions,
    group_members,
    group_pack,
    groups,
    inference,
    licence,
    organizations,
    producer_exports as portal_producer_exports,
    producer_volumes,
    retention as portal_retention,
    space_limits,
    subject_access,
    super_admins,
    team,
    usage,
    user,
    vm_credentials as portal_vm_credentials,
    vm_profiles as portal_vm_profiles,
)
from app.security.node_token import require_node_token

router = APIRouter()

router.include_router(auth.router, tags=['Authentication'])
# Before the OAuth router: its ``/auth/{provider}`` catch-all would
# otherwise match ``/auth/oidc`` and reject it as an unknown provider.
# FastAPI matches in registration order, so the literal path has to win.
router.include_router(oidc.router, tags=['Authentication - OIDC'])
router.include_router(auth_methods.router, tags=['Authentication'])
router.include_router(auth_exchange.router, tags=['Authentication'])
router.include_router(oauth.router, tags=['Authentication - OAuth'])
router.include_router(chat.router, tags=['Chat'])
# igni as the identity provider for its own services: registration, the
# token endpoint, and the directory a service reads with its token. The
# discovery documents are at the root, in `app.main`. See
# docs/service-identity.md.
router.include_router(identity_clients.router, tags=['Identity'])
router.include_router(identity_token.router, tags=['Identity'])
router.include_router(identity_directory.router, tags=['Directory'])
router.include_router(identity_extensions.router)
router.include_router(identity_notifications.router)
router.include_router(identity_events.router)
router.include_router(identity_vm_profile.router)
router.include_router(identity_revocations.router)
# Documents are not served here. They live in `igni__artifacts`, which
# the CLI reaches directly with a token igni issues for it, and which
# reads people through the directory like any registered service.
#
# The notifications *feed* stays: a person's notifications span more
# than documents, and the rows live in this database.
router.include_router(notifications.router, tags=['Notifications'])

# Portal endpoints (authenticated via Bearer token)
portal_router = APIRouter()
portal_router.include_router(access_review.router, tags=['Portal - Access Review'])
portal_router.include_router(audit.router, tags=['Portal - Audit'])
portal_router.include_router(erasure.router, tags=['Portal - Erasure'])
portal_router.include_router(subject_access.router, tags=['Portal - Subject Access'])
portal_router.include_router(groups.router, tags=['Portal - Groups'])
portal_router.include_router(group_members.router, tags=['Portal - Groups'])
portal_router.include_router(group_entries.router, tags=['Portal - Groups'])
portal_router.include_router(group_extensions.router, tags=['Portal - Groups'])
portal_router.include_router(group_pack.router, tags=['Portal - Groups'])
portal_router.include_router(portal_apps.router, tags=['Portal - Apps'])
portal_router.include_router(portal_vm_profiles.router, tags=['Portal - VM profiles'])
portal_router.include_router(portal_vm_credentials.router, tags=['Portal - VM credentials'])
portal_router.include_router(portal_api_keys.router, tags=['Portal - API keys'])
portal_router.include_router(space_limits.router, tags=['Portal - Spaces'])
portal_router.include_router(producer_volumes.router, tags=['Portal - VM profiles'])
portal_router.include_router(portal_retention.router, tags=['Portal - Retention'])
portal_router.include_router(portal_producer_exports.router, tags=['Portal - Retention'])
portal_router.include_router(inference.router, tags=['Portal - Inference'])
portal_router.include_router(usage.router, tags=['Portal - Usage'])
portal_router.include_router(team.router, tags=['Portal - Team'])
portal_router.include_router(user.router, tags=['Portal - User'])
portal_router.include_router(organizations.router, tags=['Portal - Organizations'])
portal_router.include_router(super_admins.router, tags=['Portal - Super admins'])
portal_router.include_router(licence.router, tags=['Portal - Licence'])
portal_router.include_router(portal_deployment.router, tags=['Portal - Deployment'])
portal_router.include_router(portal_clients.router, tags=['Portal - Registered clients'])
portal_router.include_router(extension_priority.router, tags=['Portal - Extension priority'])
portal_router.include_router(portal_authorize.router, tags=['Portal - Sign in to a client'])
router.include_router(portal_router, prefix='/portal')

# Node registration, called by whatever starts and stops the customer's
# GPU clusters. The node-token dependency is attached to the subtree
# rather than per route, so a new verb cannot be added unauthenticated by
# omission — and an unset token 404s the whole thing.
nodes_router = APIRouter(dependencies=[Depends(require_node_token)])
nodes_router.include_router(nodes.router, tags=['Inference Nodes'])
router.include_router(nodes_router)

# The cloud's gateway and node agent: platform clients, acting for the
# whole deployment with a `platform` token (docs/service-identity.md §11).
platform_router = APIRouter(prefix='/platform')
platform_router.include_router(platform_vm_commands.router, tags=['Platform - VMs'])
platform_router.include_router(platform_producer_events.router, tags=['Platform - Events'])
platform_router.include_router(platform_producers.router, tags=['Platform - Producers'])
platform_router.include_router(platform_producer_exports.router, tags=['Platform - Retention'])
platform_router.include_router(platform_vm_credentials.router, tags=['Platform - VM credentials'])
platform_router.include_router(platform_vm_resets.router, tags=['Platform - VM resets'])
router.include_router(platform_router)

# Commands in producers' VMs, asked for by producers, org admins and the
# super admin (docs/service-identity.md §13).
router.include_router(cloud_vm_commands.router, tags=['VM commands'])
# A person's MCP approvals, read by igni in their cloud VM (§14).
router.include_router(cloud_mcp_approvals.router, tags=['MCP approvals'])
# A person's own events, fetched by igni on their desktop (§15).
router.include_router(cloud_my_events.router, tags=['Events'])
# An app's spaces: a producer per end user of the app (§17).
router.include_router(cloud_spaces.router, tags=['Spaces'])
# An app asking for its VM's profile, and reading what it has (§12.3).
router.include_router(cloud_app_vm_profile.router, tags=['VM profiles'])
