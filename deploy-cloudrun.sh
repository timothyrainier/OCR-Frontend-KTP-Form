#!/usr/bin/env bash
# =============================================================================
#  Deploy "Pindai KTP & Form" ke Google Cloud Run
#  Jalankan di Google Cloud Shell dari folder proyek:   bash deploy-cloudrun.sh
#  Aman dijalankan ulang (untuk update kode): yang sudah ada tidak dibuat ulang.
# =============================================================================
set -euo pipefail

# ----------------------------- PENGATURAN ------------------------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-southeast2}"              # Jakarta
SERVICE="${SERVICE:-pindai-ktp}"
OCR_MODE="${OCR_MODE:-documentai}"               # documentai | mock
DOCAI_LOCATION="${DOCAI_LOCATION:-us}"           # Document AI hanya tersedia di us / eu
DOCAI_PROCESSOR_ID="${DOCAI_PROCESSOR_ID:-}"     # kosong = cari / buat Form Parser otomatis
DB_MODE="${DB_MODE:-cloudsql}"                   # cloudsql | external
EXTERNAL_DATABASE_URL="${EXTERNAL_DATABASE_URL:-}"   # dipakai bila DB_MODE=external (Neon/Supabase)
SQL_INSTANCE="${SQL_INSTANCE:-pindai-db}"
SQL_TIER="${SQL_TIER:-db-f1-micro}"
ADMIN_USER="${ADMIN_USER:-admin}"
BUCKET="${BUCKET:-${PROJECT_ID}-pindai-uploads}"
MAX_INSTANCES="${MAX_INSTANCES:-3}"
# ------------------------------------------------------------------------------

SA_NAME="${SERVICE}-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SECRET_KEY_NAME="${SERVICE}-secret-key"
SECRET_USERS_NAME="${SERVICE}-auth-users"
SECRET_DB_NAME="${SERVICE}-database-url"
NEW_ADMIN_PASSWORD=""

step() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
die()  { printf "\n\033[1;31mGAGAL: %s\033[0m\n" "$*" >&2; exit 1; }
secret_exists() { gcloud secrets describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1; }
secret_create() { printf %s "$2" | gcloud secrets create "$1" --project "$PROJECT_ID" --replication-policy=automatic --data-file=- >/dev/null; }

[ -n "$PROJECT_ID" ] || die "Project belum dipilih. Jalankan: gcloud config set project ID_PROJECT"
[ -f Dockerfile ] || die "Jalankan skrip ini dari folder proyek (yang berisi Dockerfile)."
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
echo "Project: $PROJECT_ID ($PROJECT_NUMBER), region: $REGION, service: $SERVICE"

step "1/8 Mengaktifkan API yang dibutuhkan (bisa 1-2 menit)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  documentai.googleapis.com storage.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com sqladmin.googleapis.com

step "2/8 Service account untuk aplikasi"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="Pindai KTP (Cloud Run)"
for role in roles/documentai.apiUser roles/secretmanager.secretAccessor roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" \
    --condition=None >/dev/null
done
# Agar aplikasi bisa membuat signed URL tanpa file kunci JSON
gcloud iam service-accounts add-iam-policy-binding "$SA" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null

# Build dari source memakai service account Compute default; pastikan punya izin build
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None >/dev/null 2>&1 || \
  echo "(Lewati izin build untuk Compute default SA; bila build gagal karena izin, lihat DEPLOY-CLOUDRUN.md)"

step "3/8 Bucket Cloud Storage untuk file upload: gs://$BUCKET"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null

step "4/8 Processor Document AI (Form Parser)"
if [ "$OCR_MODE" = "documentai" ] && [ -z "$DOCAI_PROCESSOR_ID" ]; then
  TOKEN=$(gcloud auth print-access-token)
  API="https://${DOCAI_LOCATION}-documentai.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DOCAI_LOCATION}/processors"
  HDR=(-H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT_ID" -H "Content-Type: application/json")
  DOCAI_PROCESSOR_ID=$(curl -s "${HDR[@]}" "$API" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(next((p["name"].split("/")[-1] for p in d.get("processors", []) if p.get("displayName") == "pindai-form-parser"), ""))')
  if [ -z "$DOCAI_PROCESSOR_ID" ]; then
    DOCAI_PROCESSOR_ID=$(curl -s -X POST "${HDR[@]}" \
      -d '{"type":"FORM_PARSER_PROCESSOR","displayName":"pindai-form-parser"}' "$API" | python3 -c '
import sys, json
d = json.load(sys.stdin)
if "name" not in d:
    sys.stderr.write("Respons Document AI: " + json.dumps(d) + "\n"); sys.exit(1)
print(d["name"].split("/")[-1])') || die "Tidak bisa membuat processor Form Parser. Buat manual di Console lalu isi DOCAI_PROCESSOR_ID."
    echo "Processor Form Parser dibuat: $DOCAI_PROCESSOR_ID"
  else
    echo "Memakai processor yang sudah ada: $DOCAI_PROCESSOR_ID"
  fi
fi

step "5/8 Database"
CLOUDSQL_FLAG=()
if [ "$DB_MODE" = "cloudsql" ]; then
  if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
    echo "Membuat Cloud SQL PostgreSQL ($SQL_TIER). Ini memakan waktu 5-10 menit..."
    gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=enterprise \
      --tier="$SQL_TIER" --region="$REGION" --storage-size=10 --storage-auto-increase
  fi
  gcloud sql databases describe ocr --instance="$SQL_INSTANCE" >/dev/null 2>&1 || \
    gcloud sql databases create ocr --instance="$SQL_INSTANCE"
  CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
  CLOUDSQL_FLAG=(--add-cloudsql-instances "$CONN")
  if ! secret_exists "$SECRET_DB_NAME"; then
    DB_PASS=$(openssl rand -hex 20)
    if gcloud sql users list --instance="$SQL_INSTANCE" --format='value(name)' | grep -qx ocrapp; then
      gcloud sql users set-password ocrapp --instance="$SQL_INSTANCE" --password="$DB_PASS"
    else
      gcloud sql users create ocrapp --instance="$SQL_INSTANCE" --password="$DB_PASS"
    fi
    secret_create "$SECRET_DB_NAME" "postgresql+psycopg2://ocrapp:${DB_PASS}@/ocr?host=/cloudsql/${CONN}"
  fi
elif [ "$DB_MODE" = "external" ]; then
  [ -n "$EXTERNAL_DATABASE_URL" ] || secret_exists "$SECRET_DB_NAME" || die "Isi EXTERNAL_DATABASE_URL (contoh dari Neon)."
  if [ -n "$EXTERNAL_DATABASE_URL" ]; then
    URL=$(printf %s "$EXTERNAL_DATABASE_URL" | sed -E 's#^postgres(ql)?://#postgresql+psycopg2://#')
    if secret_exists "$SECRET_DB_NAME"; then
      printf %s "$URL" | gcloud secrets versions add "$SECRET_DB_NAME" --data-file=- >/dev/null
    else
      secret_create "$SECRET_DB_NAME" "$URL"
    fi
  fi
else
  die "DB_MODE harus cloudsql atau external"
fi

step "6/8 Secret: kunci sesi & akun login"
secret_exists "$SECRET_KEY_NAME" || secret_create "$SECRET_KEY_NAME" "$(openssl rand -hex 32)"
if ! secret_exists "$SECRET_USERS_NAME"; then
  NEW_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)
  secret_create "$SECRET_USERS_NAME" "${ADMIN_USER}:${NEW_ADMIN_PASSWORD}"
fi

step "7/8 Build & deploy ke Cloud Run (5-8 menit pada deploy pertama)"
ENV_VARS="OCR_MODE=${OCR_MODE},GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${DOCAI_LOCATION},DOCAI_PROCESSOR_ID=${DOCAI_PROCESSOR_ID}"
ENV_VARS+=",STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET},APP_TIMEZONE=Asia/Jakarta,ENABLE_DOCS=false,MAX_UPLOAD_MB=100"
gcloud run deploy "$SERVICE" --source . --region "$REGION" --quiet \
  --service-account "$SA" --allow-unauthenticated \
  --memory 1Gi --cpu 1 --cpu-boost --timeout 300 --concurrency 20 \
  --min-instances 0 --max-instances "$MAX_INSTANCES" \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "SECRET_KEY=${SECRET_KEY_NAME}:latest,AUTH_USERS=${SECRET_USERS_NAME}:latest,DATABASE_URL=${SECRET_DB_NAME}:latest" \
  ${CLOUDSQL_FLAG[@]+"${CLOUDSQL_FLAG[@]}"}

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')

step "8/8 Izin CORS bucket agar browser bisa mengunggah langsung"
CORS_FILE=$(mktemp)
cat > "$CORS_FILE" <<JSON
[{"origin": ["$URL"], "method": ["PUT", "GET"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600}]
JSON
gcloud storage buckets update "gs://$BUCKET" --cors-file="$CORS_FILE" >/dev/null
rm -f "$CORS_FILE"

printf "\n\033[1;32mSELESAI!\033[0m\n"
echo "Alamat aplikasi : $URL"
echo "Mode OCR        : $OCR_MODE (processor: ${DOCAI_PROCESSOR_ID:-tidak dipakai})"
if [ -n "$NEW_ADMIN_PASSWORD" ]; then
  echo "Login           : ${ADMIN_USER} / ${NEW_ADMIN_PASSWORD}"
  echo "                  Simpan kata sandi ini; tidak akan ditampilkan lagi."
else
  echo "Login           : akun yang sudah ada (lihat DEPLOY-CLOUDRUN.md untuk menambah/mengganti)"
fi
