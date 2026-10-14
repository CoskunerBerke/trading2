# -*- coding: utf-8 -*-
"""`scripts/extract_lab_records.py`: iş günlüğündeki kayıt blokları bayt bayt geri yazılır; her bozuklukta o kayıt
yazılmaz ve çıkış kodu 2 olur. `signal-lab.yml`deki basma adımı da (bash + coreutils varsa) uçtan uca sınanır."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TS = "2026-09-26T14:07:13.4464420Z "


def _mod():
    spec = importlib.util.spec_from_file_location("_extract_lab_records_probe", ROOT / "scripts" / "extract_lab_records.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


X = _mod()


def _payload(vid: str = "CV001_X") -> bytes:
    rec = {"record_schema": "candle_lab/2", "id": vid, "verdict": "GÜÇLÜ ADAY", "verdict_strict": "ZAYIF İZ",
           "note": "ş" * 90}
    return (json.dumps(rec, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _block(name: str, data: bytes, *, sha: str | None = None, nbytes: int | None = None) -> list[str]:
    b64 = base64.b64encode(data).decode("ascii")
    return ([f"=== LAB RECORD BEGIN {name} sha256={sha or hashlib.sha256(data).hexdigest()} bytes={len(data) if nbytes is None else nbytes}"]
            + [b64[i:i + 76] for i in range(0, len(b64), 76)] + [f"=== LAB RECORD END {name}"])


GH = "signal-lab\tPrint lab records\t"      # `gh run view --log` öneki


def _log(lines: list[str], *, ts: bool = False, crlf: bool = False, gh: bool = False, trail: str = "") -> str:
    noise = ["##[group]Run shopt -s nullglob", 'echo "=== LAB RECORD BEGIN $n sha256=$sha bytes=$bytes"', "##[endgroup]"]
    out = [(GH if gh else "") + (TS if ts or gh else "") + x + trail for x in noise + lines + ["Post job cleanup."]]
    return ("\r\n" if crlf else "\n").join(out) + ("\r\n" if crlf else "\n")


def _run(tmp_path, text: str, *args: str) -> tuple[int, Path]:
    log = tmp_path / "job.log"
    log.write_bytes(text.encode("utf-8"))
    out = tmp_path / "out"
    return X.main([str(log), "--out-dir", str(out), *args]), out


@pytest.mark.parametrize("ts, crlf", [(False, False), (True, False), (True, True), (False, True)])
def test_record_written_byte_for_byte(tmp_path, capsys, ts, crlf):
    data = _payload()
    rc, out = _run(tmp_path, _log(_block("CV001_X.json", data), ts=ts, crlf=crlf))
    assert rc == 0 and (out / "CV001_X.json").read_bytes() == data
    assert capsys.readouterr().out.count("OK  CV001_X.json") == 1


@pytest.mark.parametrize("gh, trail", [(True, ""), (True, "  "), (False, " \t"), (True, " ")])
def test_gh_cli_prefix_and_trailing_space(tmp_path, gh, trail):
    """`gh run view --log` satırı "<iş>\\t<adım>\\t<zaman> <satır>"dır; sondaki boşluk da kaydı bozmamalı."""
    data = _payload()
    rc, out = _run(tmp_path, _log(_block("CV001_X.json", data), gh=gh, trail=trail, crlf=bool(trail)))
    assert rc == 0 and (out / "CV001_X.json").read_bytes() == data


def test_two_records_and_dry_run(tmp_path, capsys):
    a, b = _payload("CV001_X"), _payload("CV002_Y")
    text = _log(_block("CV001_X.json", a) + ["arada başka satır"] + _block("CV002_Y.json", b), ts=True)
    rc, out = _run(tmp_path, text, "--dry-run")
    assert rc == 0 and not out.exists()
    assert "deneme" in capsys.readouterr().out
    rc, out = _run(tmp_path, text)
    assert rc == 0 and (out / "CV001_X.json").read_bytes() == a and (out / "CV002_Y.json").read_bytes() == b
    assert sorted(p.name for p in out.iterdir()) == ["CV001_X.json", "CV002_Y.json"]


def test_corrupted_base64_is_refused(tmp_path, capsys):
    good, data = _payload("CV002_Y"), _payload()
    blk = _block("CV001_X.json", data)
    blk[1] = blk[1][:10] + "!" + blk[1][11:]
    rc, out = _run(tmp_path, _log(blk + _block("CV002_Y.json", good)))
    assert rc == 2 and not (out / "CV001_X.json").exists() and (out / "CV002_Y.json").read_bytes() == good
    assert "RED CV001_X.json" in capsys.readouterr().out
    blk = _block("CV001_X.json", data)
    blk[2] = blk[2][:-4] + ("A" if blk[2][-4] != "A" else "B") + blk[2][-3:]      # geçerli alfabe, yanlış içerik
    rc, out = _run(tmp_path, _log(blk))
    assert rc == 2 and not (out / "CV001_X.json").exists()


def test_wrong_sha_or_byte_count_is_refused(tmp_path):
    data = _payload()
    rc, out = _run(tmp_path, _log(_block("CV001_X.json", data, sha="0" * 64)))
    assert rc == 2 and not (out / "CV001_X.json").exists()
    rc, out = _run(tmp_path, _log(_block("CV001_X.json", data, nbytes=len(data) + 1)))
    assert rc == 2 and not (out / "CV001_X.json").exists()


def test_truncated_block_is_refused(tmp_path, capsys):
    data = _payload()
    blk = _block("CV001_X.json", data)
    rc, out = _run(tmp_path, _log(blk[:-1]))                                     # END yok (günlük kesildi)
    assert rc == 2 and not (out / "CV001_X.json").exists()
    rc, out = _run(tmp_path, _log(blk[:3] + _block("CV002_Y.json", _payload("CV002_Y"))))   # ortasında yeni BEGIN
    assert rc == 2 and not (out / "CV001_X.json").exists() and (out / "CV002_Y.json").exists()
    rc, out = _run(tmp_path, _log(blk[:-1] + ["=== LAB RECORD END CV009_Z.json"]))           # END adı başka
    assert rc == 2 and not (out / "CV001_X.json").exists()
    assert "yarım blok" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["../evil.json", "sub/CV001_X.json", "..\\evil.json", "CV001_X.txt", "CV 1.json"])
def test_traversal_or_bad_name_is_refused(tmp_path, name):
    rc, out = _run(tmp_path, _log(_block(name, _payload())))
    assert rc == 2 and (not out.exists() or not any(out.iterdir()))
    assert not (tmp_path / "evil.json").exists()


def test_duplicate_name_is_refused(tmp_path):
    a = _payload()
    rc, out = _run(tmp_path, _log(_block("CV001_X.json", a) + _block("CV001_X.json", a)))
    assert rc == 2 and not (out / "CV001_X.json").exists()


def test_no_blocks(tmp_path):
    rc, out = _run(tmp_path, _log([]))
    assert rc == 1 and not out.exists()


def test_workflow_print_step_round_trips(tmp_path):
    """`signal-lab.yml` "Print lab records" adımının GERÇEK betiği → günlük → çıkarıcı: bayt bayt aynı."""
    if sys.platform.startswith("win") or not all(shutil.which(c) for c in ("bash", "sha256sum", "base64", "wc", "cut", "tr")):
        pytest.skip("bash + coreutils yok")
    wf = (ROOT / ".github" / "workflows" / "signal-lab.yml").read_text(encoding="utf-8")
    m = re.search(r"- name: Print lab records\n        if: always\(\)\n        run: \|\n((?:          .*\n)+)", wf)
    assert m, "basma adımı bulunamadı"
    script = "".join(line[10:] + "\n" for line in m.group(1).splitlines())
    work = tmp_path / "w"
    (work / "signal_lab_out").mkdir(parents=True)
    empty = subprocess.run(["bash", "-e", "-c", script], cwd=work, capture_output=True, text=True, check=True)
    assert empty.stdout == "", "kayıt yoksa hiçbir şey basılmaz"
    recs = work / "signal_lab_out" / "variation_records"
    recs.mkdir()
    files = {"CV001_X.json": _payload("CV001_X"), "CV002_Y.json": _payload("CV002_Y") * 7}
    for n, d in files.items():
        (recs / n).write_bytes(d)
    res = subprocess.run(["bash", "-e", "-c", script], cwd=work, capture_output=True, check=True)
    text = "".join(TS + line + "\r\n" for line in res.stdout.decode("ascii").splitlines())
    assert all(len(line) <= 76 for line in res.stdout.decode("ascii").splitlines() if not line.startswith("==="))
    rc, out = _run(tmp_path, text)
    assert rc == 0 and {p.name: p.read_bytes() for p in out.iterdir()} == files
