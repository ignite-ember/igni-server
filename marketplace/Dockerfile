# The Google Cloud Marketplace deployer image.
#
# F109. Marketplace does not install a Helm chart directly: it runs this
# container in the customer's cluster, which renders the chart with the
# answers from the form described in `schema.yaml` and applies them.
#
# `deployer_helm` is Google's base image and it expects two things at
# fixed paths: the chart as a tarball under /data/chart, and the schema
# at /data/schema.yaml. Everything else — rendering, the Application
# object, reporting progress back to the console — is the base image's
# job.
#
# ## Building it
#
#   # From the repository root, because the chart is a sibling directory.
#   docker build -f marketplace/Dockerfile -t "$REGISTRY/igni/deployer:$TAG" .
#
# ## Not verified
#
# This has not been built against the real base image, run through
# `mpdev`, or submitted. The base image tag below is the one Google
# documents and needs checking against the current release before a
# submission — see marketplace/README.md for the whole list of what a
# person has to do that this repository cannot.

FROM gcr.io/cloud-marketplace-tools/k8s/deployer_helm:0.13

# The chart, as the base image expects to find it.
COPY charts/igni /data/chart/igni

# The form. `schema.yaml` is read by the marketplace UI *and* by the
# deployer, which is why it lives beside the chart rather than inside it.
COPY marketplace/schema.yaml /data/schema.yaml

# Provenance labels, the same ones the application images carry. A
# scanner reads these, and an unlabelled deployer is the one image in a
# listing that nobody thought about.
LABEL org.opencontainers.image.title="igni marketplace deployer" \
      org.opencontainers.image.description="Renders the igni Helm chart from Google Cloud Marketplace parameters" \
      org.opencontainers.image.source="https://github.com/ignite-ember/igni-server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Ignite Ember"
