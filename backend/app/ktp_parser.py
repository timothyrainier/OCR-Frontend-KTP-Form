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
    "kelurahan_desa": r"Kel\.?\s*/\s*Desa|Kelurahan\s*/\s*Desa",
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
GOLDAR = ["A", "B", "AB", "O", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "-"]
# Golongan darah + rhesus opsional ("O+", "AB-"); "0" sering terbaca untuk "O"
GOLDAR_RE = r"(?:AB|A|B|O|0)\s*[+\-]?|-"


def _norm_goldar(g: str) -> str:
    g = re.sub(r"\s", "", (g or "").upper()).replace("0", "O")
    return g if g in GOLDAR else "-"

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


PEKERJAAN = [
    "BELUM/TIDAK BEKERJA", "MENGURUS RUMAH TANGGA", "PELAJAR/MAHASISWA", "PENSIUNAN", "PEGAWAI NEGERI SIPIL",
    "TENTARA NASIONAL INDONESIA", "KEPOLISIAN RI", "PERDAGANGAN", "PETANI/PEKEBUN", "PETERNAK",
    "NELAYAN/PERIKANAN", "INDUSTRI", "KONSTRUKSI", "TRANSPORTASI", "KARYAWAN SWASTA", "KARYAWAN BUMN",
    "KARYAWAN BUMD", "KARYAWAN HONORER", "BURUH HARIAN LEPAS", "BURUH TANI/PERKEBUNAN", "PEMBANTU RUMAH TANGGA",
    "TUKANG BATU", "TUKANG KAYU", "TUKANG JAHIT", "PENATA RAMBUT", "MEKANIK", "SENIMAN", "TABIB", "PERANCANG BUSANA",
    "PENTERJEMAH", "IMAM MASJID", "PENDETA", "PASTOR", "WARTAWAN", "USTADZ/MUBALIGH", "JURU MASAK", "PROMOTOR ACARA",
    "ANGGOTA DPRD", "DOSEN", "GURU", "PILOT", "PENGACARA", "NOTARIS", "ARSITEK", "AKUNTAN", "KONSULTAN", "DOKTER",
    "BIDAN", "PERAWAT", "APOTEKER", "PSIKIATER/PSIKOLOG", "PENYIAR TELEVISI", "PENYIAR RADIO", "PELAUT", "PENELITI",
    "SOPIR", "PEDAGANG", "PERANGKAT DESA", "KEPALA DESA", "BIARAWATI", "WIRASWASTA", "LAINNYA",
]
TEXT_FIELDS = ["nama", "alamat", "kelurahan_desa", "kecamatan"]
DATE_RE = re.compile(r"\b\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{2,4}\b")


def _strip_value(line: str) -> str:
    return _clean(re.sub(r"^\s*[:;]+", "", line))


def _label_only(line: str) -> bool:
    """Baris yang hanya berisi label (mis. "Agama" atau "Status Perkawinan :")."""
    rest = ANY_LABEL.sub("", line)
    return bool(ANY_LABEL.search(line)) and not re.sub(r"[\s:;.\-]", "", rest)


def _classify(v: str):
    """Tebak jenis nilai dari isinya. Mengembalikan (jenis, nilai_baku, skor)."""
    u = v.upper().strip()
    compact = re.sub(r"\s", "", u)
    digits = re.sub(r"\D", "", compact.translate(DIGIT_FIX))
    if len(digits) == 16 and len(compact) <= 18 and sum(c.isdigit() for c in compact) >= 12:
        return "nik", digits, 1.0
    if re.fullmatch(r"\d{1,3}\s*/\s*\d{1,3}", u.translate(DIGIT_FIX)):
        a, b = re.split(r"\s*/\s*", u.translate(DIGIT_FIX))
        return "rt_rw", f"{int(a):03d}/{int(b):03d}", 1.0
    gd = re.fullmatch(rf"(?:GOL\.?\s*DARAH\s*[:;]?\s*)?({GOLDAR_RE})", u)
    if gd:
        return "golongan_darah", _norm_goldar(gd.group(1)), 1.0
    if _closest(v.replace("l", "I").upper(), ["SEUMUR HIDUP"], 0.75)[0]:
        return "seumur_hidup", "SEUMUR HIDUP", 1.0
    if re.fullmatch(r"W\s*N\s*[I1l|]|W\s*N\s*A", u):
        return "kewarganegaraan", "WNA" if u.endswith("A") else "WNI", 1.0
    m = DATE_RE.search(u.translate(DIGIT_FIX))   # terjemahan 1:1 per karakter, posisi tetap sama
    if m:
        rest = _clean(u[: m.start()] + " " + u[m.end():])   # bagian non-tanggal tetap huruf asli
        d = normalize_date(m.group(0))
        if d and re.search(r"[A-Z]{3,}", rest):
            return "ttl", (rest.strip(" ,"), d), 1.0
        if d and not rest:
            return "date", d, 1.0
    jk = re.match(r"(LAKI[\s\-]*LAKI|PEREMPUAN|LAKI|PRIA|WANITA)\b(.*)", u)
    if jk or _closest(u.split(" GOL")[0], JK, 0.8)[0]:
        base = u.split(" GOL")[0]
        val = _closest(base, JK, 0.5)[0] or ("PEREMPUAN" if "PEREMPUAN" in base or "WANITA" in base else "LAKI-LAKI")
        gm = re.search(rf"DARAH\s*[:;]?\s*({GOLDAR_RE})(?![A-Z0-9])", u)
        return "jenis_kelamin", (val, _norm_goldar(gm.group(1)) if gm else None), 1.0
    # Untuk pencocokan pilihan baku, rapikan salah baca umum: l/1/| -> I, 0 -> O
    fuzzy = v.replace("l", "I").replace("|", "I").upper().replace("1", "I").replace("0", "O")
    for kind, opts, cutoff in (("agama", AGAMA, 0.8), ("status_perkawinan", STATUS, 0.85), ("pekerjaan", PEKERJAAN, 0.85)):
        best, score = _closest(fuzzy, opts, cutoff)
        if best:
            return kind, best, score
    return "text", u, 1.0


def parse_ktp(text: str, kv_pairs=None, base_conf: float = 0.9, line_confs: dict = None) -> dict:
    """Susun teks OCR KTP menjadi field.

    Document AI sering membaca KTP per kolom: semua label dulu, lalu semua nilai, dan urutannya bisa
    bergeser. Karena itu nilai dikenali terutama dari isinya (NIK 16 digit, tanggal, agama, dll.), lalu
    sisa teks bebas (Nama, Alamat, Kel/Desa, Kecamatan) diisi menurut urutan kemunculannya.
    """
    text = text or ""
    line_confs = line_confs or {}
    lines = [l for l in (s.strip() for s in text.splitlines()) if l]
    out = {k: {"value": "", "confidence": base_conf, "warning": None} for k in KTP_KEYS}
    conf_of = lambda l: float(line_confs.get(l.strip(), base_conf))  # noqa: E731

    def put(key, value, conf, warn=None):
        if value and not out[key]["value"]:
            out[key].update(value=value, confidence=conf, warning=warn)
            return True
        return False

    used = set()

    # 1. Judul: PROVINSI ... / KABUPATEN ... / KOTA ...
    for idx, l in enumerate(lines[:5]):
        u = l.upper()
        if "PROVINSI" in u and not out["provinsi"]["value"]:
            put("provinsi", _clean(re.sub(r".*PROVINSI", "", u)), conf_of(l)); used.add(idx)
        elif re.match(r"^(KABUPATEN|KOTA|KAB\.?)\b", u) and not out["kabupaten_kota"]["value"]:
            put("kabupaten_kota", _clean(u.replace("KAB.", "KABUPATEN")), conf_of(l)); used.add(idx)
        elif re.match(r"^JAKARTA\b", u) and not out["kabupaten_kota"]["value"]:
            put("kabupaten_kota", _clean(u), conf_of(l)); used.add(idx)

    def apply(kind, val, conf, raw=""):
        """Masukkan nilai hasil klasifikasi ke field yang sesuai (bila masih kosong)."""
        if kind == "nik":
            return put("nik", val, conf if raw.strip().isdigit() else min(conf, 0.85))
        if kind == "ttl":
            ok = put("tempat_lahir", val[0], conf)
            return put("tanggal_lahir", val[1], conf) or ok
        if kind == "jenis_kelamin":
            ok = put("jenis_kelamin", val[0], conf)
            if val[1]:
                put("golongan_darah", val[1], conf)
            return ok
        if kind == "seumur_hidup":
            return put("berlaku_hingga", "SEUMUR HIDUP", conf)
        if kind in ("rt_rw", "golongan_darah", "kewarganegaraan", "agama", "status_perkawinan", "pekerjaan"):
            return put(kind, val, conf)
        return False

    # 2. Label dan nilai di baris yang sama ("Agama : ISLAM")
    for idx, l in enumerate(lines):
        if idx in used or _label_only(l):
            continue
        for key in LABELS:
            m = re.search(rf"(?:{LABELS[key]})\s*[:;]?\s*(.*)", l, re.I)
            if not m:
                continue
            rest = m.group(1)
            nxt = ANY_LABEL.search(rest)
            if nxt:
                rest = rest[: nxt.start()]
            val = _clean(rest)
            if not val:
                continue
            kind, norm, score = _classify(val)
            target = "tempat_lahir" if key == "ttl" else key
            c = conf_of(l) * (0.6 + 0.4 * score)
            if key == "ttl" and kind == "ttl":
                apply(kind, norm, c)
            elif key == "berlaku_hingga":
                put(key, "SEUMUR HIDUP" if kind == "seumur_hidup" else (normalize_date(val) or val.upper()), c)
            elif kind == key or (key == "jenis_kelamin" and kind == "jenis_kelamin"):
                apply(kind, norm, c, val)
            elif key in TEXT_FIELDS or key == "pekerjaan":
                put(target, val.upper(), c)
            else:
                put(target, val.upper(), c * 0.6, "Nilai tidak sesuai pilihan baku")
            used.add(idx)
        # Gol. Darah sering ikut di baris jenis kelamin
        gm = re.search(rf"Gol\.?\s*Darah\s*[:;]?\s*({GOLDAR_RE})(?![A-Za-z0-9])", l, re.I)
        if gm:
            put("golongan_darah", _norm_goldar(gm.group(1)), conf_of(l))

    # 3. Nilai yang terpisah dari labelnya: kenali dari isi
    cands = []   # (index, kind, value, conf, raw)
    for idx, l in enumerate(lines):
        if idx in used or _label_only(l):
            continue
        v = _strip_value(l)
        if not v:
            continue
        kind, norm, score = _classify(v)
        if kind == "text":
            letters = re.sub(r"[^A-Za-z0-9]", "", v)
            if len(letters) < 3 or re.search(r"^(GOL|DARAH|KTP|NIK)\b", v, re.I):
                continue  # sisa potongan label / noise
        colon = bool(re.match(r"^\s*[:;]", l))   # nilai di kolom KTP diawali ":"; teks lain (tanda tangan, watermark) tidak
        cands.append((idx, kind, norm, conf_of(l) * (0.6 + 0.4 * score), v, colon))

    dates = [c for c in cands if c[1] == "date"]
    for c in cands:
        if c[1] not in ("text", "date"):
            apply(c[1], c[2], c[3], c[4])
    if dates:
        # Berlaku hingga: tanggal di kolom nilai (diawali ":"), atau yang tanggal-bulannya sama dengan
        # tanggal lahir (KTP lama berlaku sampai hari ulang tahun). Tanggal terbit: sisanya, yang terakhir.
        birth = out["tanggal_lahir"]["value"][:5]
        berlaku = None
        if not out["berlaku_hingga"]["value"] and len(dates) >= 2:
            berlaku = next((d for d in dates if d[5]), None) or \
                next((d for d in dates if birth and d[2][:5] == birth and d[2] != out["tanggal_lahir"]["value"]), None) or dates[0]
            put("berlaku_hingga", berlaku[2], berlaku[3])
        rest_dates = [d for d in dates if d is not berlaku]
        if rest_dates:
            terbit = next((d for d in reversed(rest_dates) if not d[5]), rest_dates[-1])
            put("tanggal_terbit", terbit[2], terbit[3])

    # 4. Kota terbit: teks terakhir yang setelah blok nilai (biasanya sama dengan nama kabupaten/kota)
    texts = [c for c in cands if c[1] == "text"]
    typed_idx = [c[0] for c in cands if c[1] in ("agama", "status_perkawinan", "pekerjaan", "kewarganegaraan")]
    kab = re.sub(r"^(KABUPATEN|KOTA)\s+", "", out["kabupaten_kota"]["value"])
    kab_full = out["kabupaten_kota"]["value"]
    if kab_full:
        same = [t for t in texts if not t[5] and max(difflib.SequenceMatcher(None, t[2], kab_full).ratio(),
                                                     difflib.SequenceMatcher(None, t[2], kab).ratio()) >= 0.85]
        if same:
            put("kota_terbit", same[-1][2], same[-1][3])
            texts = [t for t in texts if t not in same]
    if texts and not out["kota_terbit"]["value"]:
        lastc = texts[-1]
        near_date = dates and lastc[0] < dates[-1][0]
        same_kab = kab and difflib.SequenceMatcher(None, lastc[2], kab).ratio() >= 0.8
        after_typed = typed_idx and lastc[0] > max(typed_idx)
        text_full = all(out[k]["value"] for k in TEXT_FIELDS)
        if near_date and (same_kab or after_typed) and (len(texts) > 1 or text_full):
            put("kota_terbit", lastc[2], lastc[3])
            texts = texts[:-1]

    # 5. Pekerjaan di luar daftar: teks di antara status/agama dan kewarganegaraan
    if not out["pekerjaan"]["value"]:
        lo = [c[0] for c in cands if c[1] in ("agama", "status_perkawinan")]
        hi = [c[0] for c in cands if c[1] == "kewarganegaraan"]
        if lo and hi:
            mid = [t for t in texts if max(lo) < t[0] < min(hi)]
            if mid:
                put("pekerjaan", mid[0][2], mid[0][3])
                texts = [t for t in texts if t is not mid[0]]

    # 6. Pasangan key-value Form Parser sebagai cadangan untuk teks bebas
    for key in TEXT_FIELDS:
        if not out[key]["value"]:
            val, c = _from_kv(kv_pairs, key)
            if val and _classify(val)[0] == "text" and not _label_only(val):
                put(key, val.upper(), c if c is not None else base_conf)
                texts = [t for t in texts if t[2] != val.upper()]

    # 7. Sisa teks bebas menurut urutan: Nama, Alamat (bisa >1 baris), Kel/Desa, Kecamatan
    empty = [k for k in TEXT_FIELDS if not out[k]["value"]]
    room = len(empty) + (1 if "alamat" in empty else 0)      # alamat boleh 2 baris
    if len(texts) > len(empty):
        colon_texts = [t for t in texts if t[5]]
        if len(colon_texts) >= len(empty):
            texts = colon_texts                               # buang teks tanpa ":" (tanda tangan, watermark)
    if len(texts) > len(empty):                               # buang teks yang jelas jauh kurang yakin dari lainnya
        confs_sorted = sorted(t[3] for t in texts)
        median = confs_sorted[len(confs_sorted) // 2]
        while len(texts) > len(empty):
            worst = min(texts, key=lambda t: t[3])
            if worst[3] < 0.7 and worst[3] < median - 0.2:
                texts.remove(worst)
            else:
                break
    while len(texts) > room:                                  # masih kelebihan: buang yang paling tidak yakin
        texts.remove(min(texts, key=lambda t: t[3]))
    if texts and empty:
        vals = [t[2] for t in texts]
        confs = [t[3] for t in texts]
        assign = {}
        if empty == TEXT_FIELDS and len(vals) >= 4:
            assign = {"nama": (vals[0], confs[0]), "kecamatan": (vals[-1], confs[-1]),
                      "kelurahan_desa": (vals[-2], confs[-2]),
                      "alamat": (" ".join(vals[1:-2]), min(confs[1:-2]))}
        else:
            for k, v, c in zip(empty, vals, confs):
                assign[k] = (v, c * 0.9)
            if "alamat" in empty and len(vals) > len(empty):   # baris lebih: gabungkan ke alamat
                ai = empty.index("alamat")
                extra = len(vals) - len(empty)
                assign["alamat"] = (" ".join(vals[ai: ai + 1 + extra]), min(confs[ai: ai + 1 + extra]) * 0.9)
                for k, v, c in zip(empty[ai + 1:], vals[ai + 1 + extra:], confs[ai + 1 + extra:]):
                    assign[k] = (v, c * 0.9)
        for k, (v, c) in assign.items():
            put(k, v, c)

    # 8. NIK cadangan: deret 16 digit di mana saja
    if not out["nik"]["value"]:
        m = re.search(r"(?<![0-9A-Z])[0-9OoIlDBSZG|]{16}(?![0-9A-Z])", text)
        if m:
            put("nik", re.sub(r"\D", "", m.group(0).translate(DIGIT_FIX)), base_conf * 0.85)
    return _finalize(out)


def _finalize(out: dict):
    """Validasi silang (NIK vs provinsi/tanggal lahir/jenis kelamin) dan susun hasil akhir."""
    nik = out["nik"]["value"]
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


def build_ktp(values: dict, confs: dict = None, default_conf: float = 0.9):
    """Susun field KTP dari nilai yang sudah dipetakan per field (mis. hasil Gemini).

    Nilai tetap dirapikan ke format baku (NIK 16 digit, tanggal dd-mm-yyyy, pilihan baku agama/status/
    jenis kelamin) lalu divalidasi silang sama seperti hasil Document AI.
    """
    confs = confs or {}
    out = {k: {"value": "", "confidence": default_conf, "warning": None} for k in KTP_KEYS}

    def raw(k):
        return _clean(str(values.get(k) or "")).upper()

    def c(k):
        v = confs.get(k)
        try:
            return max(0.0, min(float(v), 1.0)) if v is not None else default_conf
        except (TypeError, ValueError):
            return default_conf

    def put(k, v, conf=None, warn=None):
        out[k].update(value=v, confidence=c(k) if conf is None else conf, warning=warn)

    for k in ("provinsi", "kabupaten_kota", "nama", "tempat_lahir", "alamat", "kelurahan_desa",
              "kecamatan", "kota_terbit"):
        v = re.sub(r"\s+", " ", raw(k))
        if k == "provinsi":
            v = _clean(re.sub(r"^PROVINSI\b", "", v))
        if v:
            put(k, v)

    v = raw("nik")
    if v:
        digits = re.sub(r"\D", "", v.replace(" ", "").translate(DIGIT_FIX))
        put("nik", digits, c("nik") if digits == re.sub(r"\s", "", v) else min(c("nik"), 0.85))

    for k in ("tanggal_lahir", "tanggal_terbit"):
        v = raw(k)
        if v:
            d = normalize_date(v)
            put(k, d or v, None if d else min(c(k), 0.6), None if d else "Format tanggal tidak dikenali")

    v = raw("berlaku_hingga")
    if v:
        if "SEUMUR" in v or "HIDUP" in v:
            put("berlaku_hingga", "SEUMUR HIDUP")
        else:
            d = normalize_date(v)
            put("berlaku_hingga", d or v, None if d else min(c("berlaku_hingga"), 0.6))

    v = raw("rt_rw")
    if v:
        m = re.match(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$", v.translate(DIGIT_FIX))
        put("rt_rw", f"{int(m.group(1)):03d}/{int(m.group(2)):03d}" if m else v,
            None if m else min(c("rt_rw"), 0.6), None if m else "Format RT/RW tidak sesuai (000/000)")

    v = raw("golongan_darah")
    if v:
        put("golongan_darah", _norm_goldar(v.replace("0", "O")))

    v = raw("kewarganegaraan")
    if v:
        put("kewarganegaraan", "WNA" if v.startswith("WNA") or "ASING" in v else "WNI" if v.startswith("WN") or "INDONESIA" in v else v)

    for k, opts in (("jenis_kelamin", JK), ("agama", AGAMA), ("status_perkawinan", STATUS), ("pekerjaan", PEKERJAAN)):
        v = raw(k)
        if not v:
            continue
        if k == "jenis_kelamin" and v in ("PRIA", "LAKI LAKI", "LAKI"):
            v = "LAKI-LAKI"
        if k == "jenis_kelamin" and v == "WANITA":
            v = "PEREMPUAN"
        best, score = _closest(v, opts, 0.8)
        if best:
            put(k, best, c(k) * (0.6 + 0.4 * score) if best != v else None)
        elif k == "pekerjaan":
            put(k, v)          # daftar pekerjaan KTP tidak lengkap; nilai di luar daftar tetap sah
        else:
            put(k, v, min(c(k), 0.6), "Nilai tidak sesuai pilihan baku")

    return _finalize(out)
