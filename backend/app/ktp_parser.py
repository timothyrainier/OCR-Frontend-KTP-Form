"""Menyusun teks hasil OCR KTP menjadi field terstruktur + validasi.

Sumber input:
  - text        : teks penuh dokumen dari Document AI (document.text)
  - kv_pairs    : pasangan key/value dari Form Parser (opsional, dipakai lebih dulu)
  - base_conf   : confidence rata-rata OCR (0..1)
"""
import difflib
import re
from datetime import date

# Urutan & label field KTP sesuai tata letak kartu
KTP_FIELDS = [
    ("provinsi", "Provinsi", "Wilayah"),
    ("kabupaten_kota", "Kabupaten/Kota", "Wilayah"),
    ("nik", "NIK", "Identitas"),
    ("nama", "Nama", "Identitas"),
    ("tempat_lahir", "Tempat Lahir", "Identitas"),
    ("tanggal_lahir", "Tanggal Lahir", "Identitas"),
    ("jenis_kelamin", "Jenis Kelamin", "Identitas"),
    ("golongan_darah", "Gol. Darah", "Identitas"),
    ("alamat", "Alamat", "Alamat"),
    ("rt_rw", "RT/RW", "Alamat"),
    ("kelurahan_desa", "Kel/Desa", "Alamat"),
    ("kecamatan", "Kecamatan", "Alamat"),
    ("agama", "Agama", "Lainnya"),
    ("status_perkawinan", "Status Perkawinan", "Lainnya"),
    ("pekerjaan", "Pekerjaan", "Lainnya"),
    ("kewarganegaraan", "Kewarganegaraan", "Lainnya"),
    ("berlaku_hingga", "Berlaku Hingga", "Lainnya"),
    ("kota_terbit", "Kota Terbit", "Penerbitan"),
    ("tanggal_terbit", "Tanggal Terbit", "Penerbitan"),
]
KTP_KEYS = [k for k, _, _ in KTP_FIELDS]

# Pola label (toleran terhadap salah baca OCR)
LABELS = {
    "nik": r"\bN\s*[I1l]\s*K\b",
    "nama": r"\bNama\b",
    "ttl": r"Tempat\s*/?\s*Tgl\.?\s*Lahir|Tempat\s*/?\s*Tanggal\s*Lahir|Tmpt\s*/?\s*Tgl\s*Lahir",
    "jenis_kelamin": r"Jenis\s*Kelamin",
    "golongan_darah": r"Gol\.?\s*Darah",
    "alamat": r"\bAlamat\b",
    "rt_rw": r"\bRT\s*/\s*RW\b",
    "kelurahan_desa": r"Kel\s*/\s*Desa|Kelurahan|\bDesa\b",
    "kecamatan": r"Kecamatan",
    "agama": r"\bAgama\b",
    "status_perkawinan": r"Status\s*Perkawinan",
    "pekerjaan": r"Pekerjaan",
    "kewarganegaraan": r"Kewarganegaraan",
    "berlaku_hingga": r"Berlaku\s*Hingga",
}
ANY_LABEL = re.compile("|".join(f"(?:{p})" for p in LABELS.values()), re.I)

AGAMA = ["ISLAM", "KRISTEN", "KATOLIK", "HINDU", "BUDDHA", "KONGHUCU",
         "KEPERCAYAAN TERHADAP TUHAN YME"]
STATUS = ["BELUM KAWIN", "KAWIN", "CERAI HIDUP", "CERAI MATI"]
JK = ["LAKI-LAKI", "PEREMPUAN"]
GOLDAR = ["A", "B", "AB", "O", "-"]

KODE_PROVINSI = {
    "11": "ACEH", "12": "SUMATERA UTARA", "13": "SUMATERA BARAT", "14": "RIAU", "15": "JAMBI",
    "16": "SUMATERA SELATAN", "17": "BENGKULU", "18": "LAMPUNG", "19": "KEPULAUAN BANGKA BELITUNG",
    "21": "KEPULAUAN RIAU", "31": "DKI JAKARTA", "32": "JAWA BARAT", "33": "JAWA TENGAH",
    "34": "DI YOGYAKARTA", "35": "JAWA TIMUR", "36": "BANTEN", "51": "BALI",
    "52": "NUSA TENGGARA BARAT", "53": "NUSA TENGGARA TIMUR", "61": "KALIMANTAN BARAT",
    "62": "KALIMANTAN TENGAH", "63": "KALIMANTAN SELATAN", "64": "KALIMANTAN TIMUR",
    "65": "KALIMANTAN UTARA", "71": "SULAWESI UTARA", "72": "SULAWESI TENGAH",
    "73": "SULAWESI SELATAN", "74": "SULAWESI TENGGARA", "75": "GORONTALO", "76": "SULAWESI BARAT",
    "81": "MALUKU", "82": "MALUKU UTARA", "91": "PAPUA BARAT", "92": "PAPUA BARAT DAYA",
    "94": "PAPUA", "95": "PAPUA SELATAN", "96": "PAPUA TENGAH", "97": "PAPUA PEGUNUNGAN",
}

DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1",
                           "i": "1", "L": "1", "Z": "2", "z": "2", "S": "5", "s": "5", "G": "6",
                           "b": "6", "T": "7", "B": "8", "g": "9", "q": "9"})


def looks_like_ktp(text: str) -> bool:
    t = (text or "").upper()
    hits = sum(bool(re.search(p, t, re.I)) for p in
               (LABELS["nik"], LABELS["ttl"], LABELS["jenis_kelamin"], LABELS["kewarganegaraan"], r"PROVINSI"))
    return hits >= 3


def _clean(v: str) -> str:
    v = re.sub(r"^[\s:;.\-=]+", "", v or "")
    v = re.sub(r"\s+", " ", v)
    return v.strip(" :;,-").strip()


def _closest(value: str, options, cutoff=0.6):
    if not value:
        return None, 0.0
    v = value.upper().replace(" ", "")
    best, score = None, 0.0
    for opt in options:
        s = difflib.SequenceMatcher(None, v, opt.replace(" ", "")).ratio()
        if s > score:
            best, score = opt, s
    return (best, score) if score >= cutoff else (None, score)


def _from_lines(text: str, label_key: str) -> str:
    """Ambil nilai di baris yang sama setelah label; berhenti sebelum label berikutnya."""
    pat = re.compile(rf"(?:{LABELS[label_key]})\s*[:;]?\s*(.*)", re.I)
    for line in text.splitlines():
        m = pat.search(line)
        if m:
            rest = m.group(1)
            nxt = ANY_LABEL.search(rest)
            if nxt and nxt.start() > 0:
                rest = rest[: nxt.start()]
            elif nxt and nxt.start() == 0:
                rest = ""
            return _clean(rest)
    return ""


def _from_kv(kv_pairs, label_key: str) -> tuple:
    for kv in kv_pairs or []:
        if re.search(LABELS[label_key], kv.get("key", ""), re.I):
            val = kv.get("value", "") or ""
            nxt = ANY_LABEL.search(val)
            if nxt and nxt.start() > 0:
                val = val[: nxt.start()]
            return _clean(val), kv.get("confidence")
    return "", None


def normalize_date(v: str):
    m = re.search(r"(\d{1,2})\s*[-/.\s]\s*(\d{1,2})\s*[-/.\s]\s*(\d{2,4})", (v or "").translate(DIGIT_FIX))
    if not m:
        return None
    d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y <= date.today().year % 100 else 1900
    try:
        date(y, mth, d)
    except ValueError:
        return None
    return f"{d:02d}-{mth:02d}-{y:04d}"


def parse_ktp(text: str, kv_pairs=None, base_conf: float = 0.9) -> dict:
    text = text or ""
    lines = [l for l in (s.strip() for s in text.splitlines()) if l]
    out = {k: {"value": "", "confidence": base_conf, "warning": None} for k in KTP_KEYS}

    def get(label_key):
        val, conf = _from_kv(kv_pairs, label_key)
        if not val:
            val = _from_lines(text, label_key)
        return val, (conf if conf is not None else base_conf)

    # Provinsi & Kabupaten/Kota (baris judul)
    for l in lines[:4]:
        u = l.upper()
        if "PROVINSI" in u and not out["provinsi"]["value"]:
            out["provinsi"]["value"] = _clean(re.sub(r".*PROVINSI", "", u))
        elif re.match(r"^(KABUPATEN|KOTA|KAB\.?)\b", u) and not out["kabupaten_kota"]["value"]:
            out["kabupaten_kota"]["value"] = _clean(u.replace("KAB.", "KABUPATEN"))
        elif u.startswith("JAKARTA") and not out["kabupaten_kota"]["value"]:
            out["kabupaten_kota"]["value"] = _clean(u)

    # NIK
    nik_raw, conf = get("nik")
    if not re.sub(r"\D", "", nik_raw.translate(DIGIT_FIX)):
        m = re.search(r"\b[0-9OoIlDBSZG|]{16}\b", text)
        nik_raw = m.group(0) if m else nik_raw
    nik = re.sub(r"\D", "", nik_raw.translate(DIGIT_FIX))
    out["nik"].update(value=nik, confidence=conf)
    if nik and nik_raw.strip() != nik:
        out["nik"]["confidence"] = min(conf, 0.85)

    # Field berlabel biasa
    for key in ("nama", "alamat", "kecamatan", "pekerjaan"):
        v, c = get(key)
        out[key].update(value=v.upper(), confidence=c)

    v, c = get("kelurahan_desa")
    out["kelurahan_desa"].update(value=v.upper(), confidence=c)

    # Tempat / tanggal lahir
    ttl, c = get("ttl")
    if ttl:
        parts = re.split(r"\s*,\s*", ttl, maxsplit=1)
        tempat = parts[0] if len(parts) == 2 else re.sub(r"[\d\-/.\s]+$", "", ttl)
        tgl = normalize_date(parts[1] if len(parts) == 2 else ttl)
        out["tempat_lahir"].update(value=_clean(tempat).upper(), confidence=c)
        out["tanggal_lahir"].update(value=tgl or "", confidence=c if tgl else c * 0.5)
        if not tgl:
            out["tanggal_lahir"]["warning"] = "Format tanggal tidak dikenali"

    # Pilihan tetap (dicocokkan ke daftar baku)
    for key, options, label in (("jenis_kelamin", JK, "jenis_kelamin"), ("agama", AGAMA, "agama"),
                                ("status_perkawinan", STATUS, "status_perkawinan")):
        v, c = get(label)
        best, score = _closest(v, options)
        if best:
            out[key].update(value=best, confidence=round(c * (0.6 + 0.4 * score), 4))
        else:
            out[key].update(value=v.upper(), confidence=c * 0.5)
            if v:
                out[key]["warning"] = "Nilai tidak sesuai pilihan baku"

    v, c = get("golongan_darah")
    v = v.upper().replace("0", "O").strip()
    out["golongan_darah"].update(value=v if v in GOLDAR else (v or "-"), confidence=c if v in GOLDAR else c * 0.7)

    v, c = get("rt_rw")
    m = re.search(r"(\d{1,3})\s*/\s*(\d{1,3})", v.translate(DIGIT_FIX))
    out["rt_rw"].update(value=f"{int(m.group(1)):03d}/{int(m.group(2)):03d}" if m else v, confidence=c)

    v, c = get("kewarganegaraan")
    u = v.upper()
    out["kewarganegaraan"].update(value="WNI" if "WNI" in u or "INDONESIA" in u else ("WNA" if "WNA" in u else u),
                                  confidence=c)

    v, c = get("berlaku_hingga")
    if re.search(r"SEUMUR|HIDUP", v, re.I) or _closest(v, ["SEUMUR HIDUP"], 0.6)[0]:
        out["berlaku_hingga"].update(value="SEUMUR HIDUP", confidence=c)
    else:
        out["berlaku_hingga"].update(value=normalize_date(v) or v.upper(), confidence=c)

    # Kota & tanggal terbit: biasanya 2 baris terakhir (KOTA lalu dd-mm-yyyy)
    for i in range(len(lines) - 1, 0, -1):
        tgl = normalize_date(lines[i]) if re.fullmatch(r"[\dOoIl\-/.\s]{8,12}", lines[i]) else None
        if tgl:
            out["tanggal_terbit"].update(value=tgl)
            prev = lines[i - 1]
            if not ANY_LABEL.search(prev) and not re.search(r"\d", prev):
                out["kota_terbit"].update(value=_clean(prev).upper())
            break

    # ---------------------------------------------------------- validasi silang
    warnings = []
    if not nik:
        out["nik"]["warning"] = "NIK tidak terbaca"
    elif len(nik) != 16:
        out["nik"]["warning"] = f"NIK harus 16 digit (terbaca {len(nik)})"
        out["nik"]["confidence"] = min(out["nik"]["confidence"], 0.5)
    else:
        prov = KODE_PROVINSI.get(nik[:2])
        if not prov:
            out["nik"]["warning"] = "Kode provinsi pada NIK tidak dikenal"
        elif out["provinsi"]["value"] and difflib.SequenceMatcher(None, prov, out["provinsi"]["value"]).ratio() < 0.7:
            out["provinsi"]["warning"] = f"Kode NIK menunjukkan provinsi {prov}"
        dd, mm, yy = int(nik[6:8]), int(nik[8:10]), int(nik[10:12])
        female = dd > 40
        if out["jenis_kelamin"]["value"] in JK and (out["jenis_kelamin"]["value"] == "PEREMPUAN") != female:
            out["jenis_kelamin"]["warning"] = "Tidak cocok dengan kode tanggal di NIK"
        tl = out["tanggal_lahir"]["value"]
        if tl:
            d2, m2, y2 = tl.split("-")
            if (int(d2), int(m2), int(y2) % 100) != (dd - 40 if female else dd, mm, yy):
                out["tanggal_lahir"]["warning"] = "Tidak cocok dengan tanggal lahir di NIK"

    for k in KTP_KEYS:
        f = out[k]
        if not f["value"] and k not in ("kota_terbit", "tanggal_terbit"):
            f["warning"] = f["warning"] or "Tidak terbaca"
            f["confidence"] = 0.0
        if f["warning"]:
            f["confidence"] = min(f["confidence"], 0.6)
        elif f["value"] and f["confidence"] < 0.7:
            f["warning"] = "Akurasi rendah, periksa kembali"
        f["confidence"] = round(float(f["confidence"] or 0), 4)

    missing = [lbl for k, lbl, _ in KTP_FIELDS if not out[k]["value"] and k not in ("kota_terbit", "tanggal_terbit")]
    if missing:
        warnings.append(f"{len(missing)} field tidak terbaca: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}")
    flagged = [lbl for k, lbl, _ in KTP_FIELDS if out[k]["warning"] and out[k]["value"]]
    if flagged:
        warnings.append(f"Periksa field: {', '.join(flagged)}")

    items = [{"key": k, "label": lbl, "group": grp, **out[k]} for k, lbl, grp in KTP_FIELDS]
    core = [i for i in items if i["key"] not in ("kota_terbit", "tanggal_terbit")]
    confidence = round(sum(i["confidence"] for i in core) / len(core), 4)
    return {"items": items, "tables": []}, confidence, warnings
