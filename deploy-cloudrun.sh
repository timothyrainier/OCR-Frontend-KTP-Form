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
DB_MODE="${DB_MODE:-sqlite}"                     # sqlite (gratis) | external (Neon dll.) | cloudsql
EXTERNAL_DATABASE_URL="${EXTERNAL_DATABASE_URL:-}"   # dipakai bila DB_MODE=external (Neon/Supabase)
SQL_INSTANCE="${SQL_INSTANCE:-pindai-db}"
SQL_TIER="${SQL_TIER:-db-f1-micro}"
ADMIN_USER="${ADMIN_USER:-admin}"                # hanya untuk ACCESS_MODE=password
# Akses: iap = hanya akun Google yang diizinkan (tidak publik)   |   password = publik + login aplikasi
ACCESS_MODE="${ACCESS_MODE:-iap}"
# Siapa yang boleh membuka (ACCESS_MODE=iap), pisahkan koma. Contoh:
#   user:budi@terralogiq.com,group:tim-ocr@terralogiq.com,domain:terralogiq.com
ALLOWED_MEMBERS="${ALLOWED_MEMBERS:-user:$(gcloud config get-value account 2>/dev/null)}"
# IP publik kantor (CIDR, pisahkan koma). Kosong = tidak dibatasi IP.
OFFICE_IPS="${OFFICE_IPS:-}"
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

# --- Pemberian izin IAM -------------------------------------------------------
# Bila akun Anda tidak boleh mengubah IAM (mis. hanya Editor), perintah yang belum bisa
# dijalankan dikumpulkan lalu ditulis ke perintah-untuk-admin.sh untuk dijalankan Owner project.
PENDING=()
has_binding() {  # $1 = output get-iam-policy (json), $2 = role, $3 = member
  printf %s "$1" | python3 -c '
import sys, json
role, member = sys.argv[1], sys.argv[2]
try: pol = json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if any(b.get("role") == role and member in b.get("members", []) for b in pol.get("bindings", [])) else 1)' "$2" "$3"
}
grant() {  # $1 = project|sa|bucket, $2 = resource, $3 = member, $4 = role
  local kind=$1 res=$2 member=$3 role=$4 pol="" cmd=""
  case $kind in
    project) pol=$(gcloud projects get-iam-policy "$res" --format=json 2>/dev/null || true)
             cmd="gcloud projects add-iam-policy-binding $res --member=$member --role=$role --condition=None" ;;
    sa)      pol=$(gcloud iam service-accounts get-iam-policy "$res" --format=json 2>/dev/null || true)
             cmd="gcloud iam service-accounts add-iam-policy-binding $res --member=$member --role=$role" ;;
    bucket)  pol=$(gcloud storage buckets get-iam-policy "$res" --format=json 2>/dev/null || true)
             cmd="gcloud storage buckets add-iam-policy-binding $res --member=$member --role=$role" ;;
  esac
  if [ "${SKIP_IAM_CHECK:-0}" = "1" ] || has_binding "$pol" "$role" "$member"; then
    echo "  ok  $role -> ${member#*:}"; return 0
  fi
  if $cmd >/dev/null 2>&1; then echo "  +   $role -> ${member#*:}"; return 0; fi
  echo "  !!  $role -> ${member#*:} (butuh admin)"
  PENDING+=("$cmd")
}

[ -n "$PROJECT_ID" ] || die "Project belum dipilih. Jalankan: gcloud config set project ID_PROJECT"
[ -f Dockerfile ] || die "Jalankan skrip ini dari folder proyek (yang berisi Dockerfile)."
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
echo "Project: $PROJECT_ID ($PROJECT_NUMBER), region: $REGION, service: $SERVICE"

step "1/8 Mengaktifkan API yang dibutuhkan (bisa 1-2 menit)"
APIS="run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com documentai.googleapis.com"
APIS+=" storage.googleapis.com iamcredentials.googleapis.com iap.googleapis.com"
NEED_SECRETS=0
{ [ "$ACCESS_MODE" = "password" ] || [ "$DB_MODE" != "sqlite" ]; } && NEED_SECRETS=1
[ "$NEED_SECRETS" = "1" ] && APIS+=" secretmanager.googleapis.com"
[ "$DB_MODE" = "cloudsql" ] && APIS+=" sqladmin.googleapis.com"
# shellcheck disable=SC2086
gcloud services enable $APIS

step "2/8 Service account untuk aplikasi"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="Pindai KTP (Cloud Run)"
APP_ROLES="roles/documentai.apiUser"
[ "$NEED_SECRETS" = "1" ] && APP_ROLES+=" roles/secretmanager.secretAccessor"
[ "$DB_MODE" = "cloudsql" ] && APP_ROLES+=" roles/cloudsql.client"
for role in $APP_ROLES; do
  grant project "$PROJECT_ID" "serviceAccount:$SA" "$role"
done
# Agar aplikasi bisa membuat signed URL tanpa file kunci JSON
grant sa "$SA" "serviceAccount:$SA" roles/iam.serviceAccountTokenCreator
# Build dari source memakai service account Compute default
grant project "$PROJECT_ID" "serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" roles/run.builder

step "3/8 Bucket Cloud Storage untuk file upload: gs://$BUCKET"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
grant bucket "gs://$BUCKET" "serviceAccount:$SA" roles/storage.objectAdmin

if [ ${#PENDING[@]} -gt 0 ]; then
  {
    echo "#!/usr/bin/env bash"
    echo "# Jalankan oleh Owner / Project IAM Admin project $PROJECT_ID (di Cloud Shell)."
    echo "# Memberi izin minimum yang dibutuhkan aplikasi Pindai KTP & Form."
    echo "set -e"
    printf '%s\n' "${PENDING[@]}"
    echo "echo 'Izin selesai diberikan.'"
  } > perintah-untuk-admin.sh
  printf "\n\033[1;33mBERHENTI SEMENTARA: akun %s belum boleh memberi izin IAM.\033[0m\n" "$(gcloud config get-value account 2>/dev/null)"
  echo "Perintah yang dibutuhkan sudah ditulis ke: perintah-untuk-admin.sh"
  echo ""
  cat perintah-untuk-admin.sh
  echo ""
  echo "Langkah berikutnya:"
  echo "  1. Minta Owner project menjalankan perintah di atas (atau memberi Anda peran 'Project IAM Admin')."
  echo "  2. Setelah itu jalankan ulang:  bash deploy-cloudrun.sh"
  echo "     Bila Anda tidak bisa membaca IAM project, pakai:  SKIP_IAM_CHECK=1 bash deploy-cloudrun.sh"
  exit 2
fi

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
if [ "$DB_MODE" = "sqlite" ]; then
  echo "SQLite + Litestream: database disimpan & direplikasi ke gs://$BUCKET/db/ (gratis, tanpa Cloud SQL)"
elif [ "$DB_MODE" = "cloudsql" ]; then
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
  die "DB_MODE harus sqlite, external, atau cloudsql"
fi

step "6/8 Secret (hanya untuk mode password / database eksternal)"
if [ "$ACCESS_MODE" = "password" ]; then
  secret_exists "$SECRET_KEY_NAME" || secret_create "$SECRET_KEY_NAME" "$(openssl rand -hex 32)"
else
  echo "Tidak diperlukan: login memakai akun Google (IAP)."
fi
if [ "$ACCESS_MODE" = "password" ] && ! secret_exists "$SECRET_USERS_NAME"; then
  NEW_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)
  secret_create "$SECRET_USERS_NAME" "${ADMIN_USER}:${NEW_ADMIN_PASSWORD}"
fi

step "7/8 Build & deploy ke Cloud Run (5-8 menit pada deploy pertama)"
ENV_VARS="OCR_MODE=${OCR_MODE}@GCP_PROJECT_ID=${PROJECT_ID}@GCP_LOCATION=${DOCAI_LOCATION}@DOCAI_PROCESSOR_ID=${DOCAI_PROCESSOR_ID}"
ENV_VARS+="@STORAGE_BACKEND=gcs@GCS_BUCKET=${BUCKET}@APP_TIMEZONE=Asia/Jakarta@ENABLE_DOCS=false@MAX_UPLOAD_MB=100"
ENV_VARS+="@OFFICE_IPS=${OFFICE_IPS}"
SECRETS=""
SCALE_FLAGS=(--max-instances "$MAX_INSTANCES")
if [ "$DB_MODE" = "sqlite" ]; then
  # SQLite hanya boleh dipakai 1 instance; CPU selalu aktif agar replikasi ke Cloud Storage tidak tertunda
  ENV_VARS+="@LITESTREAM_REPLICA_URL=gcs://${BUCKET}/db/ocr.db"
  SCALE_FLAGS=(--max-instances 1 --no-cpu-throttling)
else
  SECRETS+=",DATABASE_URL=${SECRET_DB_NAME}:latest"
fi
if [ "$ACCESS_MODE" = "iap" ]; then
  ENV_VARS+="@AUTH_MODE=iap"
  # Layanan tidak bisa dipanggil langsung; IAP diaktifkan setelah deploy (lihat bawah)
  ACCESS_FLAGS=(--no-allow-unauthenticated)
  gcloud beta services identity create --service=iap.googleapis.com --project "$PROJECT_ID" >/dev/null 2>&1 || true
else
  ENV_VARS+="@AUTH_MODE=password"
  SECRETS+=",SECRET_KEY=${SECRET_KEY_NAME}:latest,AUTH_USERS=${SECRET_USERS_NAME}:latest"
  ACCESS_FLAGS=(--allow-unauthenticated)
fi
SECRET_FLAGS=()
[ -n "$SECRETS" ] && SECRET_FLAGS=(--set-secrets "${SECRETS#,}")
# "^@^" = pakai @ sebagai pemisah, karena OFFICE_IPS boleh berisi koma
gcloud beta run deploy "$SERVICE" --source . --region "$REGION" --quiet \
  --service-account "$SA" "${ACCESS_FLAGS[@]}" \
  --memory 1Gi --cpu 1 --cpu-boost --timeout 300 --concurrency 20 \
  --min-instances 0 "${SCALE_FLAGS[@]}" \
  --set-env-vars "^@^${ENV_VARS}" \
  ${SECRET_FLAGS[@]+"${SECRET_FLAGS[@]}"} \
  ${CLOUDSQL_FLAG[@]+"${CLOUDSQL_FLAG[@]}"}

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')

ADMIN_AFTER=()
run_grant() {  # $1 = member, $2 = role  (izin pada layanan Cloud Run)
  local pol
  pol=$(gcloud run services get-iam-policy "$SERVICE" --region "$REGION" --format=json 2>/dev/null || true)
  has_binding "$pol" "$2" "$1" && return 0
  gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" --member="$1" --role="$2" >/dev/null 2>&1 && return 0
  ADMIN_AFTER+=("gcloud run services add-iam-policy-binding $SERVICE --region=$REGION --member=$1 --role=$2 --project=$PROJECT_ID")
}
if [ "$ACCESS_MODE" = "iap" ]; then
  # Aktifkan IAP pada layanan (butuh peran Cloud Run Admin)
  IAP_ON=$(gcloud beta run services describe "$SERVICE" --region "$REGION" --format=json 2>/dev/null | \
    python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("metadata",{}).get("annotations",{}).get("run.googleapis.com/iap-enabled",""))' || true)
  if [ "$IAP_ON" != "true" ]; then
    gcloud beta run services update "$SERVICE" --region "$REGION" --iap --quiet >/dev/null 2>&1 || \
      ADMIN_AFTER+=("gcloud beta run services update $SERVICE --region=$REGION --iap --project=$PROJECT_ID")
  fi
  # IAP harus boleh memanggil layanan; pengguna diberi akses lewat IAP
  run_grant "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" roles/run.invoker
  IFS=',' read -ra MEMBERS <<< "$ALLOWED_MEMBERS"
  for m in "${MEMBERS[@]}"; do
    m=$(echo "$m" | xargs); [ -n "$m" ] || continue
    gcloud iap web add-iam-policy-binding --member="$m" --role=roles/iap.httpsResourceAccessor \
      --region="$REGION" --resource-type=cloud-run --service="$SERVICE" >/dev/null 2>&1 || \
      ADMIN_AFTER+=("gcloud iap web add-iam-policy-binding --member=$m --role=roles/iap.httpsResourceAccessor --region=$REGION --resource-type=cloud-run --service=$SERVICE --project=$PROJECT_ID")
  done
else
  run_grant allUsers roles/run.invoker
fi

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
if [ "$ACCESS_MODE" = "iap" ]; then
  echo "Akses           : TIDAK publik. Hanya akun Google yang diizinkan (IAP): $ALLOWED_MEMBERS"
fi
if [ -n "$OFFICE_IPS" ]; then echo "IP kantor       : $OFFICE_IPS (di luar IP ini akan ditolak)"; fi
if [ ${#ADMIN_AFTER[@]} -gt 0 ]; then
  printf '%s\n' "#!/usr/bin/env bash" "set -e" "${ADMIN_AFTER[@]}" > perintah-untuk-admin-setelah-deploy.sh
  printf "\n\033[1;33mPERHATIAN:\033[0m beberapa izin akses belum bisa diberikan oleh akun Anda.\n"
  echo "Website akan menolak akses sampai Owner project menjalankan perintah berikut"
  echo "(juga tersimpan di perintah-untuk-admin-setelah-deploy.sh):"
  echo ""
  cat perintah-untuk-admin-setelah-deploy.sh
  echo ""
fi
if [ "$ACCESS_MODE" = "password" ]; then
  if [ -n "$NEW_ADMIN_PASSWORD" ]; then
    echo "Login           : ${ADMIN_USER} / ${NEW_ADMIN_PASSWORD}"
    echo "                  Simpan kata sandi ini; tidak akan ditampilkan lagi."
  else
    echo "Login           : akun yang sudah ada (lihat DEPLOY-CLOUDRUN.md)"
  fi
else
  echo "Login           : dengan akun Google kantor (IAP)"
fi
