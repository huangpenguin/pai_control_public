#!/usr/bin/env bash
# Run interactively yourself. This does not create a Cloud project or enable billing.
set -euo pipefail
quota_only=false
if [[ $# -eq 1 && "$1" == --quota-only ]]; then
  quota_only=true
elif [[ $# -ne 0 ]]; then
  printf 'Usage: scripts/auth_adc.sh [--quota-only]\n' >&2
  exit 2
fi
cd "$(dirname "$0")/.."
if [[ -f .local/runtime.env ]]; then
  set -a
  source .local/runtime.env
  set +a
fi
: "${GOOGLE_CLOUD_PROJECT:?Set an existing project with billing, API access, and permissions}"
umask 077
pai_adc_dir="${PAI_ADC_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/pai_ai_control/gcloud}"
cloud_cli_image=gcr.io/google.com/cloudsdktool/google-cloud-cli@sha256:c631b05e8121048b29c9917bdc0a9fadd76d012f04f180a4409ec2ee534e832e
if "$quota_only"; then
  if [[ ! -f "$pai_adc_dir/application_default_credentials.json" || ! -r "$pai_adc_dir/application_default_credentials.json" ]]; then
    printf 'Saved ADC is missing or unreadable. Run scripts/auth_adc.sh to log in first.\n' >&2
    exit 1
  fi
  printf 'Using saved ADC; login is skipped.\n'
else
  mkdir -p "$pai_adc_dir"
  chmod 700 "$pai_adc_dir"
  docker run --rm -it --user "$(id -u):$(id -g)" \
    -e CLOUDSDK_CONFIG=/adc -e HOME=/tmp \
    --cap-drop ALL --security-opt no-new-privileges \
    --mount "type=bind,src=$pai_adc_dir,dst=/adc" \
    "$cloud_cli_image" gcloud auth application-default login \
    --no-launch-browser --project="$GOOGLE_CLOUD_PROJECT"
  printf 'ADC saved locally. Set PAI_ADC_PATH=%s/application_default_credentials.json\n' "$pai_adc_dir"
fi
if docker run --rm --user "$(id -u):$(id -g)" \
  -e CLOUDSDK_CONFIG=/adc -e HOME=/tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$pai_adc_dir,dst=/adc" \
  "$cloud_cli_image" gcloud auth application-default set-quota-project "$GOOGLE_CLOUD_PROJECT"; then
  printf 'Quota project configured successfully.\n'
else
  quota_status=$?
  printf 'Quota project setup failed (exit %s). ADC is already saved; no repeat login is needed. Check serviceusage.services.use permission on the existing project.\n' "$quota_status" >&2
  printf 'After permission is granted, run scripts/auth_adc.sh --quota-only.\n' >&2
  exit "$quota_status"
fi
