#!/usr/bin/env bash
# =============================================================================
#  OPSI A — dijalankan oleh Owner project (sekali saja, di Cloud Shell)
#  Memberi akun pengembang peran yang cukup untuk menjalankan deploy-cloudrun.sh
#  sendiri dari awal sampai akhir, termasuk update aplikasi berikutnya.
# =============================================================================
set -euo pipefail
PROJECT="${PROJECT:-demogcp-terra2021}"
USER_EMAIL="${USER_EMAIL:-timothy@terralogiq.com}"

ROLES=(
  roles/editor                           # membuat bucket, processor Document AI, build & deploy
  roles/resourcemanager.projectIamAdmin  # memberi izin ke service account aplikasi
  roles/run.admin                        # mengaktifkan Identity-Aware Proxy di Cloud Run
  roles/iap.admin                        # mengatur siapa yang boleh mengakses aplikasi
)
for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="user:$USER_EMAIL" --role="$ROLE" --condition=None >/dev/null
  echo "OK  $ROLE -> $USER_EMAIL"
done
echo "Selesai. $USER_EMAIL sekarang bisa menjalankan deploy-cloudrun.sh."
