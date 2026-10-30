#!/usr/bin/env bash
# =============================================================================
#  OPSI B, LANGKAH 2 — dijalankan oleh Owner project SETELAH deploy pertama selesai
#  Mengaktifkan Identity-Aware Proxy (IAP) dan menentukan siapa yang boleh mengakses.
#  Website tidak publik; hanya akun Google yang terdaftar di ALLOWED_MEMBERS.
# =============================================================================
set -euo pipefail
PROJECT="${PROJECT:-demogcp-terra2021}"
PROJECT_NUMBER="${PROJECT_NUMBER:-896204485308}"
REGION="${REGION:-asia-southeast2}"
SERVICE="${SERVICE:-pindai-ktp}"
# Pisahkan dengan koma. Contoh: group:tim-ce@terralogiq.com,user:budi@terralogiq.com
ALLOWED_MEMBERS="${ALLOWED_MEMBERS:-domain:terralogiq.com}"

gcloud services enable iap.googleapis.com --project="$PROJECT"
gcloud beta services identity create --service=iap.googleapis.com --project="$PROJECT" >/dev/null 2>&1 || true

# 1. Aktifkan IAP pada layanan Cloud Run
gcloud beta run services update "$SERVICE" --region="$REGION" --project="$PROJECT" --iap --quiet
echo "OK  IAP aktif"

# 2. IAP boleh meneruskan permintaan ke layanan
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --project="$PROJECT" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
  --role=roles/run.invoker >/dev/null && echo "OK  run.invoker untuk IAP service agent"

# 3. Pengguna yang boleh membuka aplikasi
IFS=',' read -ra MEMBERS <<< "$ALLOWED_MEMBERS"
for M in "${MEMBERS[@]}"; do
  gcloud iap web add-iam-policy-binding --member="$M" --role=roles/iap.httpsResourceAccessor \
    --region="$REGION" --resource-type=cloud-run --service="$SERVICE" --project="$PROJECT" >/dev/null
  echo "OK  akses IAP untuk $M"
done
echo "Selesai. Aplikasi bisa dibuka oleh: $ALLOWED_MEMBERS"
