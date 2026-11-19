"""Hand-written claims about what the analysed code does, each pinned to a line range.

Every range was checked by reading those exact lines back from the snapshot. A claim must be
settled by its own lines: if the deciding condition sits one line above the range, the range
is wrong. `build_cases.py` reads the text off disk, so nothing here quotes code.

Groups:
- SUPPORTED: the lines alone show the claim.
- MOVED: true claims whose evidence `build_cases.py` replaces with an unrelated function body
  (not_supported cases). The range here is where the real proof is; it goes in the note.
- RELATED_BUT_NOT: evidence from the right file, with the right names, that proves nothing.
- AMBIGUOUS: settled only under an unstated assumption, or over-generalised.
"""

from __future__ import annotations

from typing import NamedTuple

ITSD = "github:pallets/itsdangerous@672971d66a2ef9f85151e53283113f33d642dabd"
MARKUP = "github:pallets/markupsafe@4e111271b1494995708a9e878a10829b5e10c7ba"
ML = "github:kashu9313161/ML-Projects@eeac1e6ec12bf0415c95fa2d6942ea40889228bf"
SNAPSHOTS = (ITSD, MARKUP, ML)

SIGNER = "src/itsdangerous/signer.py"
TIMED = "src/itsdangerous/timed.py"
MS_INIT = "src/markupsafe/__init__.py"
API = "house-prediction-system/src/api.py"


class Claim(NamedTuple):
    snapshot: str
    claim: str
    path: str
    start: int
    end: int
    note: str = ""


SUPPORTED: list[Claim] = [
    Claim(ITSD, "SigningAlgorithm.verify_signature compares the given signature with the "
                "expected one using hmac.compare_digest, a constant-time comparison.",
          SIGNER, 15, 28),
    Claim(ITSD, "NoneAlgorithm.get_signature returns an empty signature whatever the key and "
                "value.", SIGNER, 31, 37),
    Claim(ITSD, "base64_encode produces URL-safe base64 with the trailing '=' padding stripped.",
          "src/itsdangerous/encoding.py", 20, 25),
    Claim(ITSD, "In timed.py, when max_age is given, a signature whose age is greater than max_age "
                "raises SignatureExpired.", TIMED, 137, 146),
    Claim(ITSD, "In timed.py, loads re-raises SignatureExpired at once instead of trying the next "
                "signer.", TIMED, 185, 220),
    Claim(ITSD, "In url_safe.py, dump_payload uses the zlib-compressed payload only when "
                "it is shorter, and then prefixes the base64 output with a '.'.",
          "src/itsdangerous/url_safe.py", 55, 69),
    Claim(MARKUP, "markupsafe imports _escape_inner from its _speedups module and falls back to "
                  "the pure-Python _native module when that import fails.", MS_INIT, 7, 10),
    Claim(MARKUP, "Markup's __mod__ wraps every argument in _MarkupEscapeHelper with the escape "
                  "function before substituting it into the string.", MS_INIT, 154, 165),
    Claim(MARKUP, "Markup's replace method escapes the replacement text before substituting it.",
          MS_INIT, 267, 268),
    Claim(MARKUP, "setup.py retries the build without the C extension when compiling it raises "
                  "BuildFailed.", "setup.py", 62, 71),
    Claim(ML, "api.py loads the model when the module is imported, from the hard-coded path "
              "models/house_price_app_xgb.joblib.", API, 23, 25),
    Claim(ML, "The /health endpoint returns a fixed response without checking the model or "
              "anything else.", API, 66, 72),
    Claim(ML, "The HouseInput request schema declares its numeric fields as plain int or float, "
              "with no bounds on their values.", API, 32, 59),
    Claim(ML, "create_features drops the Order and PID columns, without failing when they are "
              "missing.", "house-prediction-system/src/feature_engineering.py", 9, 17),
]

MOVED: list[Claim] = [
    Claim(ITSD, "In signer.py, verify_signature(value, sig) tries each secret key in reverse "
                "list order and accepts the value as soon as one of them verifies it.",
          SIGNER, 227, 242),
    Claim(ITSD, "Signer.derive_key raises TypeError for an unknown key derivation method.",
          SIGNER, 182, 213),
    Claim(ITSD, "base64_decode restores the '=' padding before decoding and turns decoding "
                "errors into BadData.", "src/itsdangerous/encoding.py", 28, 38),
    Claim(ITSD, "Serializer.load_payload wraps any exception raised while deserialising the "
                "payload in BadPayload.", "src/itsdangerous/serializer.py", 243, 269),
    Claim(MARKUP, "Markup.__add__ escapes the string being added before concatenating it.",
          MS_INIT, 136, 140),
    Claim(MARKUP, "Markup.__html_format__ raises ValueError when it is given a format "
                  "specification.", MS_INIT, 335, 339),
    Claim(MARKUP, "escape_silent returns an empty Markup for None instead of the text 'None'.",
          MS_INIT, 48, 61),
    Claim(ML, "DataIngestion.load_data raises FileNotFoundError when the dataset file does not "
              "exist.", "house-prediction-system/src/data_ingestion.py", 6, 19),
    Claim(ML, "data_split.py makes an 80/20 train/test split with random_state fixed at 42.",
          "house-prediction-system/src/data_split.py", 17, 21),
    Claim(ML, "train.py fits the model on log1p(SalePrice) rather than on the raw price.",
          "house-prediction-system/src/train.py", 49, 55),
]

RELATED_BUT_NOT: list[Claim] = [
    Claim(ITSD, "In signer.py, verify_signature(value, sig) compares signatures in constant time.",
          SIGNER, 227, 242,
          "Right method, but it delegates to self.algorithm.verify_signature. The constant-time "
          "hmac.compare_digest is in SigningAlgorithm.verify_signature (line 28), outside the "
          "range, and a custom algorithm passed to Signer need not use it."),
    Claim(ITSD, "TimestampSigner.unsign raises SignatureExpired when the signature is older "
                "than max_age.", TIMED, 160, 167,
          "This is TimestampSigner.validate: it passes max_age to unsign and catches "
          "BadSignature. The names match, but nothing here shows unsign comparing the age or "
          "raising SignatureExpired (that is lines 137-146)."),
    Claim(MARKUP, "markupsafe falls back to a pure-Python escape implementation when the C "
                  "speedups cannot be imported.", "src/markupsafe/_native.py", 1, 8,
          "Shows the pure-Python implementation exists, not that anything falls back to it. The "
          "try/except ImportError is in __init__.py lines 7-10."),
    Claim(MARKUP, "Markup.format escapes its arguments before substituting them.", MS_INIT, 342,
          347,
          "EscapeFormatter's constructor only stores an escape function. It doesn't show "
          "format_field calling it (line 364), nor Markup.format using EscapeFormatter "
          "(lines 323-325)."),
    Claim(ML, "api.py loads its model from models/house_price_app_xgb.joblib.", API, 114, 118,
          "Uses a module-level `model` to predict, but where and how it is loaded (lines 23-25) "
          "is outside the range."),
]

AMBIGUOUS: list[Claim] = [
    Claim(ITSD, "itsdangerous avoids SHA-1 so that it works on FIPS builds.", SIGNER, 40, 45,
          "Half true. _lazy_sha1 only defers touching hashlib.sha1 until it is called, so "
          "importing works on FIPS builds; SHA-1 is still what it returns and stays the default. "
          "'Avoids' is right about import time, wrong about use."),
    Claim(ITSD, "Signer.__init__'s separator check accepts any separator that contains no "
                "character of the base64 alphabet.", SIGNER, 144, 151,
          "The check is `self.sep in _base64_alphabet`, a substring test on bytes. A one-byte "
          "separator is rejected exactly when it is in the alphabet, but a multi-byte one like "
          "b'az' contains letters and still passes because it isn't a contiguous run of the "
          "alphabet. True only if you assume a one-character separator; the alphabet itself "
          "is defined in encoding.py."),
    Claim(MARKUP, "escape() always returns text that is safe to insert into HTML.", MS_INIT, 24,
          45,
          "Plain strings are escaped, but an object with __html__ has its output wrapped as safe "
          "without escaping. 'Always safe' holds only if every __html__ method is trustworthy, "
          "which the code assumes and cannot check."),
    Claim(MARKUP, "Markup.striptags removes every HTML tag from the markup.", MS_INIT, 199, 238,
          "Removes every closed tag and comment, but stops at the first unclosed '<' or '<!--' "
          "and keeps the rest as text; and it unescapes last, so '&lt;b&gt;' in the markup comes "
          "out as '<b>'. Whether that breaks 'every tag' depends on what you count as a tag in "
          "the markup versus in the result."),
    Claim(ML, "api.py's prediction response gives the predicted price in US dollars.", API, 120,
          126,
          "The response hard-codes currency 'USD' after expm1 of a log prediction. That is a "
          "dollar price only if the model was trained on log1p of a USD SalePrice, which is "
          "decided in train.py and the dataset, not here."),
]
