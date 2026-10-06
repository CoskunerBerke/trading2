# -*- coding: utf-8 -*-
"""BAĞIMSIZ KOŞUCU — sürekli öğrenme motoru P1a + P1b (docs/SYSTEM_LEARNING_ENGINE_V1.md §9.3, §10 P1a kabul 18).

VPS'te pytest YOKTUR. Motor sürüm betiğinin `--dry-run`'ı (ve dağıtımın ön denetimi) depo testlerinin VPS'te anlamlı
alt kümesini bu dosyayla, pytest OLMADAN, AYRI bir süreçte koşar: P1a kabul 1–8, 13, 14 ve (P1b'den) P1b depo kabul
testleri 1–11, 13–15. İki aşamanın kabul numaraları çakışmasın diye P1b'ninkiler **100 eklenerek** yazılır (101 = P1b
kabul 1, …, 115 = P1b kabul 15). P1b kabul 12 (veri birimi sözleşmesi) birim DOSYASI testidir; sürüm betiği aynı
satırları kendi `unit_contract`'ıyla ve sha256 sabitiyle denetler. Parametreli testler (ör. P1b kabul 5'in günlük
koruması ve 418/429 varyantları) bu koşucuda koşamaz; kabul 5'in alt kümesi sabitlenmiş metin kalıbı testidir:

    /opt/tradingbot/venv/bin/python -s tests/standalone/run_engine_invariants.py --tree /opt/tradingbot/engine-app

Sözleşme:
* Servis ortamı AKTARILMAZ: süreç başında `TRADINGBOT_*` ve `ENGINE_*` değişkenleri silinir; testler yalnız geçici
  klasörlerde (`--tmp`, varsayılan `tempfile.mkdtemp`) sahte VPS kurar, gerçek `/opt/tradingbot/data`'ya dokunmaz.
* Bayt kodu yazılmaz (`sys.dont_write_bytecode`; engine-app salt-okunurdur).
* Ağ kapalıdır: `AF_INET`/`AF_INET6` soket bağlantıları reddedilir (testler ağ kullanmaz; S0 soket RET denemesi
  testleri yerine-geçen kullanır).
* Küçük yerine-geçenler (f8b05fb #46–#48 deseni): `pytest` modülü (approx, raises, skip, fail, mark, fixture,
  importorskip), `tmp_path` (test başına klasör), `monkeypatch` (setattr/delattr/setenv/delenv/setitem/delitem/undo/
  context; sınıf özniteliğinde tanımlayıcı korunur), `capsys` (readouterr). `--forbid-pytest` gerçek `pytest`/`_pytest`
  import'unu engeller (depo testi koşucuyu bununla çalıştırır; koşucunun pytest'e bağımlı olmadığını kanıtlar).
* Çıktı: test başına bir satır `PASS|FAIL|SKIP [kabul] ad (süre)`, sonda özet; `--json` sonuçları yazar. Hepsi geçerse
  çıkış 0, değilse 1. Alt kümedeki bir test adı depoda yoksa FAIL'dir (sessizce atlanmaz).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.abc
import inspect
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

#: (kabul no, modül, test) — VPS'te koşan alt küme (P1a kabul 18: 1–8, 13, 14; P1b: 100 + kabul no).
SUBSET: tuple[tuple[int, str, str], ...] = (
    (1, "test_research_engine_archive", "test_closes_archive_survives_5000_history_rotation_and_reconciles"),
    (1, "test_research_engine_archive", "test_late_funding_appends_rev_and_marks_day_revized_append_only"),
    (1, "test_research_engine_archive", "test_unsettled_funding_day_becomes_eksik_after_seven_days"),
    (1, "test_research_engine_archive", "test_backdated_close_revises_previous_day_row"),
    (1, "test_research_engine_archive", "test_record_view_reconciliation_detects_a_tampered_archive"),
    (2, "test_research_engine_archive", "test_spot_partial_sales_are_separate_rows_with_one_position_group"),
    (3, "test_research_engine_archive", "test_entries_archive_survives_2000_rotation_rerun_adds_zero_and_keeps_every_kind"),
    (4, "test_research_engine_archive", "test_snapshot_wallet_identity_futures_and_spot_cash"),
    (5, "test_research_engine_daily_target", "test_rows_stay_provisional_three_days_then_final_and_look_only_on_look_night"),
    (5, "test_research_engine_daily_target", "test_best_instrument_uses_verdict_denominator_and_both_denominators_are_labelled"),
    (5, "test_research_engine_daily_target", "test_no_claim_words_on_realized_only_rows_in_header_table_and_scorecard"),
    (5, "test_research_engine_daily_target", "test_revision_is_append_only_and_status_change_alone_is_not_revize"),
    (5, "test_research_engine_daily_target", "test_short_or_long_window_is_not_a_day_and_never_a_target_day"),
    (5, "test_research_engine_daily_target", "test_turkish_lowercase_label_has_no_combining_dot"),
    (5, "test_research_engine_night", "test_summary_and_status_bounded_and_no_claim_words_on_realized_only_rows"),
    (6, "test_research_engine_daily_target",
     "test_kanitlandi_only_on_look_night_with_kesin_days_eligible_scope_live_stream_property"),
    (6, "test_research_engine_daily_target", "test_kanitlandi_negative_controls"),
    (6, "test_research_engine_daily_target",
     "test_kanitlandi_needs_the_realized_day_share_too_and_descriptive_scopes_have_no_verdict"),
    (7, "test_research_engine_daily_target", "test_scorecard_daily_equals_engine_views_spot_and_all_mark_sources"),
    (8, "test_research_engine_archive", "test_restore_marks_restored_away_not_inconsistent"),
    (8, "test_research_engine_archive", "test_after_restore_alignment_reanchors_and_days_become_final_again"),
    (13, "test_research_engine_night", "test_s0_writable_state_is_isolation_broken_and_probe_file_is_removed"),
    (13, "test_research_engine_night", "test_s0_open_socket_or_timeout_is_isolation_broken"),
    (13, "test_research_engine_night", "test_s0_wrong_memory_max_is_isolation_broken"),
    (13, "test_research_engine_night", "test_s0_skew_runs_only_s0_s1a_s7"),
    (13, "test_research_engine_night", "test_s0_paper_check_variants"),
    (13, "test_research_engine_night", "test_real_probe_functions"),
    (13, "test_research_engine_night", "test_read_git_head_variants_and_this_checkout"),
    (13, "test_research_engine_night", "test_skew_ancestor_check_with_real_git_on_this_checkout"),
    (13, "test_research_engine_night", "test_wrong_state_dir_stops_not_paper_and_ledger_flags_show_in_status"),
    (13, "test_research_engine_night", "test_root_run_against_a_non_root_research_root_is_refused_before_any_write"),
    (13, "test_research_engine_night", "test_disk_guard_real_statvfs_path_refuses_and_passes"),
    (14, "test_research_engine_daily_target", "test_rawconfig_tolerates_unknown_keys_and_records_sha"),
    # ---- P1b (100 + P1b depo kabul no)
    (101, "test_research_engine_datastore", "test_rerun_with_fake_provider_adds_zero_rows_and_keeps_checksums_and_seal"),
    (101, "test_research_engine_store", "test_rewriting_the_same_rows_adds_nothing_and_keeps_files_and_seal"),
    (102, "test_research_engine_store", "test_part_checksums_sidecars_manifest_and_seal_agree_and_seal_reads_no_part"),
    (103, "test_research_engine_store", "test_only_closed_bars_and_points_are_written"),
    (104, "test_research_engine_datastore", "test_one_series_failure_does_not_stop_the_others_and_is_retried_with_jitter"),
    (105, "test_research_engine_datastore", "test_worker_rate_line_pattern_is_pinned_to_http_py_format_strings"),
    (106, "test_research_engine_datastore", "test_engine_rest_share_never_exceeds_a_tenth_of_the_ip_limit"),
    (106, "test_research_engine_datastore", "test_data_run_rest_weight_stays_within_share_end_to_end"),
    (107, "test_research_engine_store",
     "test_priority_rest_never_overwrites_archive_archive_replaces_rest_and_diffs_are_recorded"),
    (107, "test_research_engine_datastore", "test_archive_reconciliation_replaces_rest_bars_and_records_the_diff"),
    (107, "test_research_engine_datastore",
     "test_zip_without_checksum_is_unverified_not_counted_and_upgraded_when_checksum_appears"),
    (108, "test_research_engine_store",
     "test_manifest_lag_heals_corrupt_is_quarantined_and_halt_needs_two_consecutive_failed_nights"),
    (108, "test_research_engine_datastore",
     "test_manifest_lag_heals_corrupt_part_is_refetched_and_series_halts_only_after_two_failed_nights"),
    (109, "test_research_engine_store", "test_funding_interval_is_derived_from_data_and_gaps_use_it"),
    (110, "test_research_engine_store", "test_microseconds_to_ms_for_every_2025_plus_spot_series_by_path_and_date"),
    (111, "test_research_engine_parity_seed",
     "test_seed_makes_a_consistent_copy_or_redownloads_and_never_writes_the_worker_store"),
    (113, "test_research_engine_datastore", "test_backfill_writes_progress_and_eta_and_pauses_in_4h_windows"),
    (113, "test_research_engine_datastore", "test_update_stops_starting_series_after_its_deadline"),
    (114, "test_research_engine_parity_seed", "test_store_provider_fixture_parity_with_archive_provider"),
    (115, "test_research_engine_datastore", "test_night_is_not_skipped_locked_during_backfill_and_reads_only_sealed_parts"),
)
ACCEPTANCE = (1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 113, 114, 115)
_NOTSET = object()


# ============================================================================ pytest yerine-geçenleri
class _Skip(Exception):
    pass


class _Fail(AssertionError):
    pass


class MonkeyPatch:
    def __init__(self):
        self._undo: list = []

    def setattr(self, target, name, value=_NOTSET, raising=True):
        if isinstance(target, str):
            if value is not _NOTSET:
                raise TypeError("dize hedefle iki argüman verilir")
            value = name
            path, _, name = target.rpartition(".")
            obj = None
            parts = path.split(".")
            for i in range(len(parts), 0, -1):
                try:
                    obj = importlib.import_module(".".join(parts[:i]))
                except ImportError:
                    continue
                for p in parts[i:]:
                    obj = getattr(obj, p)
                break
            if obj is None:
                raise ImportError(target)
            target = obj
        old = getattr(target, name, _NOTSET)
        if raising and old is _NOTSET:
            raise AttributeError(name)
        if inspect.isclass(target):
            old = target.__dict__.get(name, _NOTSET)
        self._undo.append(("attr", target, name, old))
        setattr(target, name, value)

    def delattr(self, target, name, raising=True):
        old = getattr(target, name, _NOTSET)
        if old is _NOTSET:
            if raising:
                raise AttributeError(name)
            return
        if inspect.isclass(target):
            old = target.__dict__.get(name, _NOTSET)
        self._undo.append(("attr", target, name, old))
        delattr(target, name)

    def setitem(self, d, key, value):
        self._undo.append(("item", d, key, d.get(key, _NOTSET)))
        d[key] = value

    def delitem(self, d, key, raising=True):
        if key not in d:
            if raising:
                raise KeyError(key)
            return
        self._undo.append(("item", d, key, d[key]))
        del d[key]

    def setenv(self, name, value, prepend=None):
        self.setitem(os.environ, name, str(value))

    def delenv(self, name, raising=True):
        self.delitem(os.environ, name, raising=raising)

    def syspath_prepend(self, path):
        self._undo.append(("syspath", list(sys.path), None, None))
        sys.path.insert(0, str(path))

    def chdir(self, path):
        self._undo.append(("cwd", os.getcwd(), None, None))
        os.chdir(path)

    def undo(self):
        while self._undo:
            kind, t, n, o = self._undo.pop()
            if kind == "attr":
                if o is _NOTSET:
                    try:
                        delattr(t, n)
                    except AttributeError:
                        pass
                else:
                    setattr(t, n, o)
            elif kind == "item":
                if o is _NOTSET:
                    t.pop(n, None)
                else:
                    t[n] = o
            elif kind == "syspath":
                sys.path[:] = t
            elif kind == "cwd":
                os.chdir(t)

    @contextlib.contextmanager
    def context(self):
        m = MonkeyPatch()
        try:
            yield m
        finally:
            m.undo()


class _Raises:
    def __init__(self, exc, match=None):
        self.exc, self.match, self.value, self.type = exc, match, None, None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise _Fail(f"DID NOT RAISE {self.exc}")
        if not issubclass(et, self.exc):
            return False
        if self.match is not None and not re.search(self.match, str(ev)):
            raise _Fail(f"{ev!r} '{self.match}' ile eşleşmedi")
        self.value, self.type = ev, et
        return True


def _raises(exc, *args, match=None, **kw):
    if args:
        fn, rest = args[0], args[1:]
        with _Raises(exc, match):
            fn(*rest, **kw)
        return None
    return _Raises(exc, match)


class _Approx:
    def __init__(self, expected, rel=None, abs=None):  # noqa: A002 — pytest imzası
        self.expected, self.rel, self.abs = expected, rel, abs

    def _close(self, a, b) -> bool:
        if isinstance(b, (list, tuple)):
            return isinstance(a, (list, tuple)) and len(a) == len(b) and all(self._close(x, y) for x, y in zip(a, b))
        if isinstance(b, dict):
            return isinstance(a, dict) and a.keys() == b.keys() and all(self._close(a[k], b[k]) for k in b)
        if a == b:
            return True
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return False
        if math.isinf(a) or math.isinf(b) or math.isnan(a) or math.isnan(b):
            return False
        rel = 1e-6 if self.rel is None and self.abs is None else (self.rel or 0.0)
        ab = 1e-12 if self.abs is None else self.abs
        return abs(a - b) <= max(rel * abs(b), ab)

    def __eq__(self, other):
        return self._close(other, self.expected)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return f"approx({self.expected!r})"


class _Mark:
    def __getattr__(self, name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k:
                return a[0]
            return lambda f: f
        return deco


def _skip(reason=""):
    raise _Skip(reason)


def _fail(reason=""):
    raise _Fail(reason)


def _importorskip(name, *a, **k):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise _Skip(f"{name} yok: {exc}") from exc


def make_pytest_shim() -> types.ModuleType:
    m = types.ModuleType("pytest")
    m.__dict__.update(approx=_Approx, raises=_raises, skip=_skip, fail=_fail, mark=_Mark(), importorskip=_importorskip,
                      fixture=lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f)), MonkeyPatch=MonkeyPatch)
    m.__dict__["__engine_standalone_shim__"] = True
    return m


class _Capsys:
    def __init__(self):
        self.out, self.err = io.StringIO(), io.StringIO()

    def readouterr(self):
        o, e = self.out.getvalue(), self.err.getvalue()
        self.out.seek(0)
        self.out.truncate(0)
        self.err.seek(0)
        self.err.truncate(0)
        return types.SimpleNamespace(out=o, err=e)


class _ForbidPytest(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "_pytest" or fullname.startswith("_pytest.") or (fullname == "pytest" and
                                                                       "pytest" not in sys.modules):
            raise ImportError(f"bağımsız koşucu: gerçek {fullname} yasak")
        return None


def _block_network() -> None:
    import socket
    real, real_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError(101, "bağımsız koşucu: ağ kapalı")
        return real(self, addr)

    def connect_ex(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            return 101
        return real_ex(self, addr)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


# ============================================================================ koşu
def run(tree: Path, tmp_root: Path, *, only: set[int] | None = None) -> list[dict]:
    results = []
    for i, (acc, mod_name, fn_name) in enumerate(SUBSET):
        if only and acc not in only:
            continue
        rec = {"acceptance": acc, "module": mod_name, "test": fn_name, "status": "FAIL", "seconds": 0.0, "detail": ""}
        t0 = time.monotonic()
        mp = MonkeyPatch()
        cap = _Capsys()
        try:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name, None)
            if fn is None:
                raise _Fail(f"test bulunamadı: {mod_name}.{fn_name}")
            kwargs = {}
            for p in inspect.signature(fn).parameters:
                if p == "tmp_path":
                    d = tmp_root / f"{i:02d}_{fn_name[:48]}"
                    d.mkdir(parents=True, exist_ok=True)
                    kwargs[p] = d
                elif p == "monkeypatch":
                    kwargs[p] = mp
                elif p == "capsys":
                    kwargs[p] = cap
                else:
                    raise _Fail(f"desteklenmeyen fixture {p!r}")
            with contextlib.redirect_stdout(cap.out), contextlib.redirect_stderr(cap.err):
                fn(**kwargs)
            rec["status"] = "PASS"
        except _Skip as exc:
            rec.update(status="SKIP", detail=str(exc)[:300])
        except BaseException as exc:  # noqa: BLE001 — her hata o testin FAIL'idir; koşu sürer
            if isinstance(exc, KeyboardInterrupt):
                raise
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            rec.update(status="FAIL", detail=("".join(tb[-4:]))[-1500:])
        finally:
            mp.undo()
        rec["seconds"] = round(time.monotonic() - t0, 2)
        results.append(rec)
        print(f"{rec['status']:<4} [{acc}] {fn_name} ({rec['seconds']} sn)", flush=True)
        if rec["status"] == "FAIL":
            print("     " + rec["detail"].replace("\n", "\n     ").rstrip(), flush=True)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Öğrenme motoru P1a + P1b bağımsız değişmez koşucusu (pytest yok)")
    ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[2]), help="kod ağacı (engine-app) kökü")
    ap.add_argument("--tmp", default=None, help="geçici kök (varsayılan: yeni tempfile klasörü; sonunda silinir)")
    ap.add_argument("--keep", action="store_true", help="geçici klasörü silme")
    ap.add_argument("--only", default=None, help="yalnız bu kabul numaraları (virgüllü, ör. 13,14)")
    ap.add_argument("--json", default=None, help="sonuçları bu dosyaya yaz")
    ap.add_argument("--forbid-pytest", dest="forbid_pytest", action="store_true", help="gerçek pytest import'unu engelle")
    ap.add_argument("--list", action="store_true", help="alt kümeyi listele ve çık")
    a = ap.parse_args(argv)
    if a.list:
        for acc, mod, fn in SUBSET:
            print(f"[{acc}] {mod}.{fn}")
        return 0
    for k in [k for k in os.environ if k.startswith(("TRADINGBOT_", "ENGINE_"))]:
        os.environ.pop(k, None)
    sys.dont_write_bytecode = True
    tree = Path(a.tree).resolve()
    sys.path[:0] = [str(tree), str(tree / "tests")]
    sys.modules["pytest"] = make_pytest_shim()
    if a.forbid_pytest:
        sys.meta_path.insert(0, _ForbidPytest())
    _block_network()
    only = {int(x) for x in a.only.split(",")} if a.only else None
    own = a.tmp is None
    tmp_root = Path(tempfile.mkdtemp(prefix="engine-invariants-")) if own else Path(a.tmp)
    tmp_root.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        results = run(tree, tmp_root, only=only)
    finally:
        if own and not a.keep:
            shutil.rmtree(tmp_root, ignore_errors=True)
    n_pass = sum(r["status"] == "PASS" for r in results)
    n_fail = sum(r["status"] == "FAIL" for r in results)
    n_skip = sum(r["status"] == "SKIP" for r in results)
    covered = sorted({r["acceptance"] for r in results if r["status"] == "PASS"})
    summary = {"schema": "engine_invariants_v1", "tree": str(tree), "passed": n_pass, "failed": n_fail, "skipped": n_skip,
               "acceptance_passed": covered, "seconds": round(time.monotonic() - t0, 1), "results": results}
    if a.json:
        Path(a.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"ÖZET: {n_pass} geçti · {n_fail} kaldı · {n_skip} atlandı · kabul {covered} · {summary['seconds']} sn")
    return 0 if n_fail == 0 and n_pass > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
