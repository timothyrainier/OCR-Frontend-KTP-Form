"""Mesin OCR berbasis Gemini.

Berbeda dengan Document AI Form Parser yang mengembalikan teks + pasangan label/nilai mentah,
Gemini diminta langsung membaca dokumen dan mengembalikan JSON per field sesuai skema di bawah.
Hasilnya kemudian tetap melewati perapian dan validasi yang sama (ktp_parser.build_ktp).

Autentikasi (lihat config.py):
  - tanpa GEMINI_API_KEY : Vertex AI dengan kredensial service account (ADC), project + location
  - dengan GEMINI_API_KEY: Gemini API memakai key tersebut
"""
import io
import json
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from . import config


class GeminiError(Exception):
    """Error yang pesannya aman ditampilkan ke pengguna."""


# ------------------------------------------------------------------ skema keluaran
class Nilai(BaseModel):
    value: str = Field(description="Teks persis seperti tertulis di dokumen. Kosongkan bila tidak ada/tidak terbaca.")
    confidence: float = Field(description="Keyakinan 0.0-1.0 bahwa nilai terbaca benar. Rendahkan bila buram, terpotong, atau ditebak.")


class KtpFields(BaseModel):
    provinsi: Nilai
    kabupaten_kota: Nilai
    nik: Nilai
    nama: Nilai
    tempat_lahir: Nilai
    tanggal_lahir: Nilai
    jenis_kelamin: Nilai
    golongan_darah: Nilai
    alamat: Nilai
    rt_rw: Nilai
    kelurahan_desa: Nilai
    kecamatan: Nilai
    agama: Nilai
    status_perkawinan: Nilai
    pekerjaan: Nilai
    kewarganegaraan: Nilai
    berlaku_hingga: Nilai
    kota_terbit: Nilai
    tanggal_terbit: Nilai


class FormField(BaseModel):
    label: str
    value: str
    confidence: float
    page: int


class TableRow(BaseModel):
    cells: List[str]


class Table(BaseModel):
    title: str
    headers: List[str]
    rows: List[TableRow]


class Hasil(BaseModel):
    doc_type: Literal["ktp", "form", "lainnya"]
    readable: bool = Field(description="false bila dokumen tidak bisa dibaca sama sekali")
    issue: str = Field(description="Bila ada masalah kualitas (buram, gelap, silau, terpotong, miring), jelaskan singkat dalam Bahasa Indonesia. Kosongkan bila tidak ada.")
    raw_text: str = Field(description="Seluruh teks yang terlihat, baris demi baris sesuai urutan baca.")
    ktp: Optional[KtpFields] = None
    form_fields: List[FormField] = []
    tables: List[Table] = []


PROMPT = """Anda adalah sistem OCR untuk dokumen Indonesia. Baca dokumen terlampir dengan teliti dan kembalikan JSON sesuai skema.

Aturan umum:
- Salin teks PERSIS seperti tertulis. Jangan menebak, melengkapi, atau mengarang nilai. Bila tidak terbaca, isi value "" dan confidence rendah.
- confidence adalah perkiraan jujur 0.0-1.0 per nilai. Gunakan < 0.7 bila ada karakter yang ragu (buram, tertutup, silau).
- raw_text berisi seluruh teks yang terlihat, baris demi baris.
- Bila dokumen sama sekali tidak terbaca, isi readable=false dan jelaskan di issue.
{jenis}

Bila dokumen adalah KTP (Kartu Tanda Penduduk), isi objek "ktp":
- provinsi: tanpa kata "PROVINSI". kabupaten_kota: lengkap, mis. "KABUPATEN LUMAJANG" atau "KOTA SURABAYA" atau "JAKARTA SELATAN".
- nik: 16 digit tanpa spasi. Perhatikan angka yang mirip huruf (0/O, 1/I, 8/B, 5/S).
- tempat_lahir dan tanggal_lahir dipisah dari "Tempat/Tgl Lahir"; tanggal format dd-mm-yyyy.
- jenis_kelamin: LAKI-LAKI atau PEREMPUAN. golongan_darah: A, B, AB, O, atau "-".
- alamat: hanya baris alamat (gabungkan bila terpotong ke baris berikut), tanpa RT/RW. rt_rw format 000/000.
- agama, status_perkawinan, pekerjaan, kewarganegaraan: sesuai tulisan di kartu.
- berlaku_hingga: "SEUMUR HIDUP" atau tanggal dd-mm-yyyy.
- kota_terbit dan tanggal_terbit: kota dan tanggal kecil di bawah foto/di atas tanda tangan.
- Jangan masukkan teks watermark, hologram, atau tanda tangan ke field mana pun.
- form_fields dan tables dibiarkan kosong untuk KTP.

Bila dokumen adalah formulir atau dokumen lain, isi "form_fields" (setiap pasangan label dan isian, sesuai urutan di dokumen, label tanpa titik dua, page = nomor halaman mulai 1) dan "tables" (setiap tabel dengan header dan baris). Biarkan "ktp" kosong (null)."""

JENIS_HINT = {
    "ktp": "- Operator menyatakan dokumen ini KTP. Isi doc_type=\"ktp\" dan objek ktp, meskipun kualitasnya buruk.",
    "form": "- Operator menyatakan dokumen ini formulir. Isi doc_type=\"form\" serta form_fields dan tables.",
    "auto": "- Tentukan sendiri jenisnya: \"ktp\" untuk KTP, \"form\" untuk formulir/dokumen lain.",
}

# Format yang diterima Gemini secara langsung. TIFF/BMP dikonversi lebih dulu.
_DIRECT = {"image/jpeg", "image/png", "image/webp", "application/pdf"}

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise GeminiError("Library google-genai belum terpasang. Jalankan: pip install google-genai") from e
    http = types.HttpOptions(timeout=config.GEMINI_TIMEOUT_SECONDS * 1000)
    if config.GEMINI_API_KEY:
        _client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=http)
    else:
        if not config.GEMINI_PROJECT_ID:
            raise GeminiError("Konfigurasi Gemini belum lengkap: isi GEMINI_PROJECT_ID / GCP_PROJECT_ID, atau GEMINI_API_KEY.")
        _client = genai.Client(vertexai=True, project=config.GEMINI_PROJECT_ID,
                               location=config.GEMINI_LOCATION or "global", http_options=http)
    return _client


def prepare_content(data: bytes, mime: str):
    """Ubah ke format yang diterima Gemini dan perkecil gambar besar.

    TIFF multi-halaman dijadikan PDF, BMP/TIFF satu halaman dijadikan PNG/JPEG.
    Gambar diperkecil ke GEMINI_MAX_IMAGE_PX sisi terpanjang: cukup untuk teks KTP, dan jauh lebih cepat.
    """
    limit = config.GEMINI_MAX_MB * 1024 * 1024
    if mime == "application/pdf":
        if len(data) > limit:
            raise GeminiError(f"PDF {len(data) / 1048576:.0f} MB melebihi batas {config.GEMINI_MAX_MB:.0f} MB untuk "
                              f"proses langsung Gemini. Pecah PDF menjadi beberapa file yang lebih kecil.")
        return data, mime

    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise GeminiError("File gambar rusak atau tidak bisa dibuka.") from e

    frames = getattr(img, "n_frames", 1)
    if frames > 1:  # TIFF multi-halaman -> PDF
        pages = []
        for i in range(min(frames, 15)):
            img.seek(i)
            pages.append(_shrink(img.convert("RGB")))
        buf = io.BytesIO()
        pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=200)
        return buf.getvalue(), "application/pdf"

    img = ImageOps.exif_transpose(img)  # foto HP sering tersimpan miring dengan tag EXIF
    small = _shrink(img)
    if mime in _DIRECT and small is img and len(data) <= limit:
        return data, mime
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue(), "image/jpeg"


def _shrink(img):
    m = config.GEMINI_MAX_IMAGE_PX
    if max(img.size) <= m:
        return img
    scale = m / max(img.size)
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))


def extract(content: bytes, mime: str, doc_type: str = "auto") -> Hasil:
    from google.genai import errors, types

    client = _get_client()
    content, mime = prepare_content(content, mime)
    prompt = PROMPT.format(jenis=JENIS_HINT.get(doc_type, JENIS_HINT["auto"]))
    try:
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=content, mime_type=mime), prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Hasil,
                max_output_tokens=8192,
            ),
        )
    except errors.APIError as e:
        raise GeminiError(_friendly(e)) from e
    except Exception as e:
        name = type(e).__name__.lower()
        if "timeout" in name or "timed out" in str(e).lower():
            raise GeminiError("Gemini tidak merespons tepat waktu. Silakan scan ulang.") from e
        if "credential" in name or "defaultcredentials" in str(e).lower():
            raise GeminiError("Kredensial Google tidak ditemukan. Di komputer sendiri, jalankan "
                              "`gcloud auth application-default login` atau isi GEMINI_API_KEY.") from e
        raise

    parsed = getattr(resp, "parsed", None)
    if parsed is None:
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            reason = ""
            try:
                reason = str(resp.candidates[0].finish_reason)
            except Exception:
                pass
            if "SAFETY" in reason or "PROHIBITED" in reason or "BLOCK" in reason:
                raise GeminiError("Gemini menolak memproses dokumen ini (filter keamanan). Coba foto lain.")
            raise GeminiError("Gemini tidak mengembalikan hasil. Silakan scan ulang.")
        try:
            parsed = Hasil.model_validate(json.loads(text.removeprefix("```json").removesuffix("```")))
        except Exception as e:
            raise GeminiError("Format jawaban Gemini tidak sesuai. Silakan scan ulang.") from e
    return parsed


def _friendly(e) -> str:
    code = getattr(e, "code", None)
    msg = (getattr(e, "message", None) or str(e) or "").strip()
    low = msg.lower()
    if code in (401, 403):
        if config.GEMINI_API_KEY:
            return "Akses Gemini ditolak. Periksa GEMINI_API_KEY dan API yang diizinkan untuk key tersebut."
        return ("Akses Gemini ditolak. Pastikan Vertex AI API aktif dan service account aplikasi "
                "punya peran Vertex AI User (roles/aiplatform.user).")
    if code == 404:
        return (f"Model {config.GEMINI_MODEL} tidak ditemukan di lokasi {config.GEMINI_LOCATION}. "
                f"Ganti GEMINI_MODEL, atau set GEMINI_LOCATION=global.")
    if code == 429:
        return "Kuota Gemini sedang habis. Tunggu sebentar lalu scan ulang."
    if code == 400:
        if "size" in low or "too large" in low or "exceed" in low:
            return "Dokumen terlalu besar untuk Gemini. Kecilkan file atau pecah PDF."
        if "page" in low:
            return "PDF terlalu banyak halaman untuk diproses sekaligus. Pecah menjadi beberapa file."
        return f"Dokumen ditolak Gemini: {msg[:200]}"
    if code and code >= 500:
        return "Layanan Gemini sedang bermasalah. Silakan scan ulang beberapa saat lagi."
    return f"Gagal memanggil Gemini: {msg[:200]}"
