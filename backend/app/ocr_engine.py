"""Mesin OCR.

process_file(path, mime, doc_type) -> dict hasil terstruktur, atau raise OcrError.

Mode diatur lewat OCR_MODE di .env:
  - mock       : hasil palsu yang realistis (termasuk sesekali gagal / kualitas rendah)
  - documentai : Google Document AI Form Parser
"""
import io
import random
import re
import time
from pathlib import Path

from . import config
from .ktp_parser import looks_like_ktp, parse_ktp

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp", ".pdf": "application/pdf",
}


class OcrError(Exception):
    """Error yang pesannya aman ditampilkan ke pengguna."""


def engine_name() -> str:
    return "documentai" if config.OCR_MODE == "documentai" else "mock"


def process_file(path: Path, mime: str, doc_type: str = "auto", filename: str = "") -> dict:
    started = time.time()
    if engine_name() == "documentai":
        text, kv_pairs, tables, base_conf = _documentai_extract(path, mime)
    else:
        text, kv_pairs, tables, base_conf, doc_type = _mock_extract(filename or path.name, doc_type)

    if not text or not text.strip():
        raise OcrError("Tidak ada teks yang terdeteksi. Pastikan dokumen terlihat jelas dan tidak terpotong.")

    if doc_type == "auto":
        doc_type = "ktp" if looks_like_ktp(text) else "form"

    if doc_type == "ktp":
        fields, confidence, warnings = parse_ktp(text, kv_pairs, base_conf)
        filled = sum(1 for i in fields["items"] if i["value"])
        if filled < 4:
            raise OcrError("Dokumen tidak dikenali sebagai KTP. Coba pilih jenis \u201cForm\u201d atau scan ulang dengan foto yang lebih jelas.")
    else:
        fields, confidence, warnings = _build_form(kv_pairs, tables, base_conf)

    if confidence < config.LOW_CONFIDENCE_THRESHOLD:
        warnings.insert(0, f"Akurasi rendah ({confidence:.0%}). Disarankan retake dengan foto yang lebih terang dan tegak lurus.")

    return {
        "status": "success",
        "doc_type": doc_type,
        "confidence": confidence,
        "fields": fields,
        "raw_text": text,
        "warnings": warnings,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _build_form(kv_pairs, tables, base_conf):
    items, seen = [], {}
    for kv in kv_pairs:
        label = re.sub(r"[:\s]+$", "", kv["key"]).strip() or "Tanpa label"
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "field"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}_{seen[key]}"
        conf = kv.get("confidence") if kv.get("confidence") is not None else base_conf
        items.append({
            "key": key, "label": label, "group": f"Halaman {kv.get('page', 1)}",
            "value": kv.get("value", "").strip(), "confidence": round(conf, 4),
            "warning": None if kv.get("value", "").strip() else "Kosong / tidak terbaca",
        })
    warnings = []
    if not items and not tables:
        warnings.append("Tidak ada pasangan field\u2013nilai yang terdeteksi. Lihat tab Teks mentah.")
    confs = [i["confidence"] for i in items] or [base_conf]
    empty = sum(1 for i in items if not i["value"])
    if empty:
        warnings.append(f"{empty} field kosong atau tidak terbaca")
    return {"items": items, "tables": tables}, round(sum(confs) / len(confs), 4), warnings


# =============================================================== Document AI
_client = None


def _documentai_extract(path: Path, mime: str):
    try:
        from google.api_core.client_options import ClientOptions
        from google.api_core import exceptions as gexc
        from google.cloud import documentai
    except ImportError as e:
        raise OcrError("Library google-cloud-documentai belum terpasang. Jalankan: pip install google-cloud-documentai") from e

    if not (config.GCP_PROJECT_ID and config.DOCAI_PROCESSOR_ID):
        raise OcrError("Konfigurasi Document AI belum lengkap (GCP_PROJECT_ID / DOCAI_PROCESSOR_ID).")

    global _client
    if _client is None:
        _client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(api_endpoint=f"{config.GCP_LOCATION}-documentai.googleapis.com"))

    content, mime = _prepare_content(path, mime)
    if config.DOCAI_PROCESSOR_VERSION:
        name = _client.processor_version_path(config.GCP_PROJECT_ID, config.GCP_LOCATION,
                                              config.DOCAI_PROCESSOR_ID, config.DOCAI_PROCESSOR_VERSION)
    else:
        name = _client.processor_path(config.GCP_PROJECT_ID, config.GCP_LOCATION, config.DOCAI_PROCESSOR_ID)

    try:
        result = _client.process_document(request=documentai.ProcessRequest(
            name=name, raw_document=documentai.RawDocument(content=content, mime_type=mime)), timeout=120)
    except gexc.InvalidArgument as e:
        raise OcrError(f"Dokumen ditolak Document AI: {e.message}") from e
    except gexc.PermissionDenied as e:
        raise OcrError("Akses Document AI ditolak. Periksa service account & izin processor.") from e
    except gexc.DeadlineExceeded as e:
        raise OcrError("Document AI tidak merespons tepat waktu. Silakan retake.") from e
    except gexc.ResourceExhausted as e:
        raise OcrError("Kuota Document AI habis. Coba lagi beberapa saat lagi.") from e
    except gexc.GoogleAPICallError as e:
        raise OcrError(f"Gagal memanggil Document AI: {e.message}") from e

    doc = result.document
    text = doc.text or ""

    def seg(layout):
        if not layout or not layout.text_anchor or not layout.text_anchor.text_segments:
            return ""
        return "".join(text[int(s.start_index or 0):int(s.end_index)] for s in layout.text_anchor.text_segments).strip()

    kv_pairs, tables, token_confs = [], [], []
    for p_idx, page in enumerate(doc.pages, start=1):
        token_confs += [t.layout.confidence for t in page.tokens if t.layout and t.layout.confidence]
        for ff in page.form_fields:
            kv_pairs.append({
                "key": seg(ff.field_name.layout if hasattr(ff.field_name, "layout") else ff.field_name),
                "value": seg(ff.field_value.layout if hasattr(ff.field_value, "layout") else ff.field_value),
                "confidence": float(ff.field_value.confidence or ff.field_name.confidence or 0) or None,
                "page": p_idx,
            })
        for t_idx, table in enumerate(page.tables, start=1):
            headers = [[seg(c.layout) for c in r.cells] for r in table.header_rows]
            rows = [[seg(c.layout) for c in r.cells] for r in table.body_rows]
            tables.append({"title": f"Tabel {t_idx} (hal. {p_idx})",
                           "headers": headers[0] if headers else [], "rows": rows})

    base_conf = sum(token_confs) / len(token_confs) if token_confs else 0.9
    return text, kv_pairs, tables, base_conf


def _prepare_content(path: Path, mime: str):
    """Document AI online processing punya batas ukuran per request.
    Gambar besar dikompres otomatis; PDF besar perlu batch processing (lihat README)."""
    data = path.read_bytes()
    limit = config.DOCAI_ONLINE_MAX_MB * 1024 * 1024
    if len(data) <= limit:
        return data, mime
    if mime == "application/pdf":
        raise OcrError(
            f"PDF {len(data) / 1048576:.0f} MB melebihi batas proses langsung Document AI "
            f"({config.DOCAI_ONLINE_MAX_MB:.0f} MB). Pecah PDF menjadi beberapa file atau aktifkan batch processing.")
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    quality, scale = 90, 1.0
    while True:
        w, h = int(img.width * scale), int(img.height * scale)
        buf = io.BytesIO()
        img.resize((w, h)).save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= limit or scale < 0.2:
            return buf.getvalue(), "image/jpeg"
        scale *= 0.8
        quality = max(70, quality - 5)


# =============================================================== MOCK
_NAMA_DEPAN = ["BUDI", "SITI", "AGUS", "DEWI", "RINA", "ANDI", "EKO", "PUTRI", "HENDRA", "NUR", "YUSUF",
               "INDAH", "RIZKY", "FAJAR", "LESTARI", "DIAN", "ADI", "MEGA", "BAYU", "AYU"]
_NAMA_BELAKANG = ["SANTOSO", "RAHAYU", "WIJAYA", "SETIAWAN", "PRATAMA", "HIDAYAT", "KURNIAWAN",
                  "SARI", "NUGROHO", "SAPUTRA", "PERMATASARI", "HALIM", "SIREGAR", "WIBOWO"]
_WILAYAH = [
    ("32", "JAWA BARAT", "01", "KABUPATEN BOGOR", ["CIBINONG", "CITEUREUP", "SUKARAJA"]),
    ("32", "JAWA BARAT", "02", "KABUPATEN SUKABUMI", ["CISAAT", "SUKARAJA", "TAKOKAK"]),
    ("31", "DKI JAKARTA", "74", "JAKARTA SELATAN", ["KEBAYORAN BARU", "TEBET", "SETIABUDI"]),
    ("33", "JAWA TENGAH", "74", "KOTA SEMARANG", ["TEMBALANG", "BANYUMANIK", "CANDISARI"]),
    ("35", "JAWA TIMUR", "78", "KOTA SURABAYA", ["GUBENG", "WONOKROMO", "RUNGKUT"]),
    ("51", "BALI", "71", "KOTA DENPASAR", ["DENPASAR BARAT", "DENPASAR SELATAN"]),
]
_PEKERJAAN = ["KARYAWAN SWASTA", "WIRASWASTA", "PELAJAR/MAHASISWA", "PEGAWAI NEGERI SIPIL",
              "MENGURUS RUMAH TANGGA", "PETANI/PEKEBUN", "GURU", "BURUH HARIAN LEPAS"]
_JALAN = ["JL. MAWAR", "JL. MELATI", "JL. RAYA BOGOR", "JL. MERDEKA", "JL. SUDIRMAN", "GG. KENANGA", "PERUM GRIYA ASRI BLOK C"]
_MOCK_ERRORS = [
    "Gambar terlalu buram, teks tidak dapat dibaca. Ambil ulang foto dengan fokus yang tajam.",
    "Dokumen tidak terdeteksi di dalam gambar. Pastikan seluruh dokumen masuk ke dalam bingkai foto.",
    "Document AI tidak merespons tepat waktu. Silakan retake.",
    "Gambar terlalu gelap atau silau. Foto ulang di tempat yang terang tanpa pantulan cahaya.",
]


def _mock_extract(filename: str, doc_type: str):
    time.sleep(config.MOCK_DELAY_SECONDS * random.uniform(0.6, 1.4))
    if random.random() < config.MOCK_FAIL_RATE:
        raise OcrError(random.choice(_MOCK_ERRORS))
    low = random.random() < config.MOCK_LOW_QUALITY_RATE
    if doc_type == "auto":
        name = filename.lower()
        doc_type = "ktp" if "ktp" in name else "form" if "form" in name else random.choice(["ktp", "ktp", "form"])
    if doc_type == "ktp":
        text, kv, conf = mock_ktp_text(low)
        return text, kv, [], conf, doc_type
    kv, tables, conf = mock_form(low)
    text = "\n".join(f"{k['key']} {k['value']}" for k in kv)
    return text, kv, tables, conf, doc_type


def _garble(s: str, rate: float) -> str:
    swaps = {"0": "O", "1": "I", "8": "B", "5": "S", "A": "4", "E": "F", "I": "l", "O": "0"}
    return "".join(swaps.get(ch, ch) if random.random() < rate else ch for ch in s)


def mock_ktp_text(low_quality=False):
    """Hasilkan teks KTP + pasangan key/value (seperti output Form Parser) dengan confidence per field."""
    kp, prov, kk, kab, kecs = random.choice(_WILAYAH)
    female = random.random() < 0.5
    d, m, y = random.randint(1, 28), random.randint(1, 12), random.randint(1965, 2005)
    nama = f"{random.choice(_NAMA_DEPAN)} {random.choice(_NAMA_BELAKANG)}"
    nik = f"{kp}{kk}{random.randint(1, 30):02d}{d + 40 if female else d:02d}{m:02d}{y % 100:02d}{random.randint(1, 9999):04d}"
    tempat = kab.replace("KABUPATEN ", "").replace("KOTA ", "")
    pairs = [
        ["NIK", nik],
        ["Nama", nama],
        ["Tempat/Tgl Lahir", f"{tempat}, {d:02d}-{m:02d}-{y}"],
        ["Jenis Kelamin", "PEREMPUAN" if female else "LAKI-LAKI"],
        ["Gol. Darah", random.choice(["A", "B", "AB", "O", "-"])],
        ["Alamat", f"{random.choice(_JALAN)} NO. {random.randint(1, 120)}"],
        ["RT/RW", f"{random.randint(1, 15):03d}/{random.randint(1, 12):03d}"],
        ["Kel/Desa", random.choice(["SUKAMAJU", "MEKARSARI", "CIBADAK", "SUKASARI", "MARGAJAYA"])],
        ["Kecamatan", random.choice(kecs)],
        ["Agama", random.choice(["ISLAM", "ISLAM", "ISLAM", "KRISTEN", "KATOLIK", "HINDU", "BUDDHA"])],
        ["Status Perkawinan", random.choice(STATUS_OPTS)],
        ["Pekerjaan", random.choice(_PEKERJAAN)],
        ["Kewarganegaraan", "WNI"],
        ["Berlaku Hingga", "SEUMUR HIDUP"],
    ]
    confs = [random.uniform(0.9, 0.995) for _ in pairs]
    overall = random.uniform(0.9, 0.98)
    if low_quality:
        overall = random.uniform(0.62, 0.8)
        for i in random.sample(range(len(pairs)), 4):
            if random.random() < 0.25:
                pairs[i][1] = ""
            else:
                pairs[i][1] = _garble(pairs[i][1], 0.35)
            confs[i] = random.uniform(0.35, 0.7)
        confs = [c if c < 0.75 else random.uniform(0.7, 0.92) for c in confs]
    jk = pairs[3][1]
    lines = [f"PROVINSI {prov}", kab]
    for k, v in pairs:
        if k == "Gol. Darah":
            lines[-1] = f"Jenis Kelamin : {jk} Gol. Darah : {v}"
            continue
        lines.append(f"{k} : {v}" if v else k)
    lines += [tempat, f"{random.randint(1, 28):02d}-{random.randint(1, 12):02d}-{random.randint(2012, 2024)}"]
    kv = [{"key": k, "value": v, "confidence": c, "page": 1} for (k, v), c in zip(pairs, confs)]
    return "\n".join(lines), kv, overall


STATUS_OPTS = ["BELUM KAWIN", "KAWIN", "KAWIN", "CERAI HIDUP"]


def mock_form(low_quality=False):
    conf = lambda: random.uniform(0.5, 0.78) if low_quality else random.uniform(0.86, 0.99)  # noqa: E731
    nama = f"{random.choice(_NAMA_DEPAN)} {random.choice(_NAMA_BELAKANG)}"
    pairs = [
        ("Nomor Formulir:", f"FRM-{random.randint(2024, 2026)}-{random.randint(10000, 99999)}"),
        ("Tanggal Pengajuan:", f"{random.randint(1, 28):02d}/{random.randint(1, 12):02d}/2026"),
        ("Nama Lengkap:", nama.title()),
        ("No. KTP:", f"32{random.randint(10**13, 10**14 - 1)}"),
        ("No. Telepon:", f"08{random.randint(1000000000, 9999999999)}"[:13]),
        ("Email:", f"{nama.split()[0].lower()}.{random.randint(10, 99)}@mail.com"),
        ("Alamat:", f"{random.choice(_JALAN).title()} No. {random.randint(1, 99)}"),
        ("Jenis Layanan:", random.choice(["Pembukaan Rekening", "Pengajuan Kredit", "Perubahan Data", "Klaim Asuransi"])),
        ("Nominal:", f"Rp {random.randint(1, 50) * 1_000_000:,}".replace(",", ".")),
        ("Tanda Tangan:", random.choice(["Ada", "Ada", "Tidak ada"])),
    ]
    kv = []
    for k, v in pairs:
        c = conf()
        if low_quality and random.random() < 0.3:
            v = _garble(v, 0.3) if random.random() < 0.7 else ""
        kv.append({"key": k, "value": v, "confidence": c, "page": 1})
    tables = [{
        "title": "Tabel 1 (hal. 1)",
        "headers": ["No", "Dokumen Lampiran", "Keterangan"],
        "rows": [["1", "Fotokopi KTP", "Ada"], ["2", "Kartu Keluarga", random.choice(["Ada", "Menyusul"])],
                 ["3", "NPWP", random.choice(["Ada", "Tidak ada"])]],
    }]
    return kv, tables, sum(x["confidence"] for x in kv) / len(kv)
