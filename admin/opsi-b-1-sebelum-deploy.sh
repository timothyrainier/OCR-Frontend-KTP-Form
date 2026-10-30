#!/usr/bin/env bash
# =============================================================================
#  OPSI B, LANGKAH 1 — dijalankan oleh Owner project SEBELUM deploy
#  Izin minimum hanya untuk service account aplikasi (bukan untuk akun pengembang).
# =============================================================================
set -euo pipefail
PROJECT="${PROJECT:-demogcp-terra2021}"
PROJECT_NUMBER="${PROJECT_NUMBER:-896204485308}"
REGION="${REGION:-asia-southeast2}"
SA="pindai-ktp-run@${PROJECT}.iam.gserviceaccount.com"
BUCKET="gs://${PROJECT}-pindai-uploads"

# 1. Memanggil OCR Document AI
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" \
  --role=roles/documentai.apiUser --condition=None >/dev/null && echo "OK  documentai.apiUser"

# 2. Membuat link upload sementara ke Cloud Storage tanpa file kunci JSON
gcloud iam service-accounts add-iam-policy-binding "$SA" --project="$PROJECT" \
  --member="serviceAccount:$SA" --role=roles/iam.serviceAccountTokenCreator >/dev/null \
  && echo "OK  iam.serviceAccountTokenCreator (pada service account itu sendiri)"

# 3. Cloud Build membangun aplikasi saat deploy
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None >/dev/null && echo "OK  run.builder (Compute default SA)"

# 4. Bucket privat untuk file upload & replika database; izin hanya pada bucket ini
gcloud storage buckets describe "$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "$BUCKET" --project="$PROJECT" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null && echo "OK  storage.objectAdmin (hanya $BUCKET)"

echo "Selesai. Pengembang bisa menjalankan: SKIP_IAM_CHECK=1 bash deploy-cloudrun.sh"
