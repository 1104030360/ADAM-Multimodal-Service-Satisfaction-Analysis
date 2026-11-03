# tests/queue/test_check_db.py — python -m analysis_queue check-db 接上真的 MySQL 8.4（CD 部署前的資料庫檢查：
# docs/rebuild/cd/PLAN.md 第 4 節第 3 點、CONTRACTS.md 第 3 節）。analysis_service/ 與 analyzer/ 共用同一份
# （tools/check_report_copies.py 檢查）；環境在同一個資料夾的 conftest.py。
# MySQL 載入的是這個服務的 tests/integration/grants.sql，所以最小權限的 adam_analysis 應該每一項都 OK。
# 收回權限、改名的測試結束時一定還原（其他測試用同一個資料庫）；另建的資料庫和帳號測完就刪。
# 和 test_queue_flow.py 一樣不 import 別的測試檔（兩種匯入模式都要能跑）。
import os
import re
from contextlib import closing

import mysql.connector
import pytest

import analysis_queue as aq

pytestmark = pytest.mark.integration
OTHER_DB = "adam_check_db"  # 另建的資料庫：少了一張表、一個程序，像 migration 還沒套用完
NARROW_USER, WIDE_USER = "adam_check_db_min", "adam_check_db_wide"  # 照 grants.sql 的最小權限、整個資料庫的權限
PASSWORD = "adam-test-only"
GONE = ("tbl_analysis_worker", "sp_insert_text_storage")  # 另建的資料庫裡沒有的（兩個服務的 grants.sql 都要它們）
SUMMARY = "check-db: result={} code={} ok={} missing={} denied={} account={} database={} needs=tests/integration/grants.sql"


def check(capsys, *argv):
    """python -m analysis_queue check-db（用 DB_* 環境變數連線）：回傳 (結束碼, 每一行)。"""
    code = aq.main(["check-db", *argv])
    return code, capsys.readouterr().out.splitlines()


def items(lines):
    """每一項的 (狀態, 項目)，不含摘要。"""
    return [tuple(line.split(": ")[0].split(None, 1)) for line in lines[:-1]]


def snapshot(db, schema):
    """這個資料庫每一張表的 CHECKSUM、AUTO_INCREMENT，和 binlog 寫到哪裡：有任何寫入（包括失敗、被撤回的 INSERT
    用掉的 AUTO_INCREMENT）就會不同。測試用的 MySQL 8.4 預設開著 binlog；關著的話只比前兩樣。
    先 CHECKSUM 再讀 AUTO_INCREMENT：剛建、還沒打開過的表，information_schema 的 AUTO_INCREMENT 是 NULL，打開之後才是 1
    （check-db 的 EXPLAIN 也會打開表，但不會改它）。"""
    query = "SELECT TABLE_NAME, AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
    db.query("SET SESSION information_schema_stats_expiry = 0")
    names = [name for name, _ in db.query(query, schema)]
    checksums = db.query(f"CHECKSUM TABLE {', '.join(f'`{schema}`.`{name}`' for name in names)}")
    binlog = db.query("SHOW BINARY LOG STATUS")
    return checksums, db.query(query, schema), binlog[0][:2] if binlog else None


def use_account(monkeypatch, user, password, database="adam"):
    monkeypatch.setenv("DB_USER", user)
    monkeypatch.setenv("DB_PASSWORD", password)
    monkeypatch.setenv("DB_NAME", database)


@pytest.fixture
def undo(db):
    """改了權限或結構的測試：登記還原的 SQL，測試結束時（就算失敗）倒著做回去；測試裡也可以先呼叫 undo.now()。"""
    db.query("SET SESSION lock_wait_timeout = 30")  # 改結構要等別的連線放掉 metadata lock：最多等 30 秒，不會一直卡住
    steps = []

    def register(sql, revert):
        db.query(sql)
        steps.append(revert)

    def now():
        while steps:
            db.query(steps.pop())

    register.now = now
    yield register
    now()


@pytest.fixture
def other_db(db):
    """另一個資料庫：照這個服務的 grants.sql 建出它用到的表（CREATE TABLE … LIKE）和程序（SHOW CREATE PROCEDURE），
    但少了 GONE 的兩個；另一個帳號照 grants.sql 給同樣的 GRANT（資料庫換掉），少的那兩個的 GRANT 套用不了，
    就像 migration 和它的 GRANT 都還沒套用。再一個帳號有整個資料庫的權限。回傳套用不了的 GRANT 的錯誤碼。"""
    def drop():
        db.query(f"DROP DATABASE IF EXISTS {OTHER_DB}")
        db.query(f"DROP USER IF EXISTS '{NARROW_USER}'@'%', '{WIDE_USER}'@'%'")  # 帳號的權限一起刪掉

    with open(aq.GRANTS_FILE, encoding="utf-8") as source:
        grants = [line.strip().rstrip(";") for line in source if line.startswith("GRANT ")]
    tables = sorted({name for grant in grants for name in re.findall(r" ON adam\.(\w+) ", grant)})
    procedures = sorted({name for grant in grants for name in re.findall(r" ON PROCEDURE adam\.(\w+) ", grant)})
    db.query("SET SESSION lock_wait_timeout = 30")
    drop()
    try:
        db.query(f"CREATE DATABASE {OTHER_DB}")
        for table in tables:
            if table not in GONE:
                db.query(f"CREATE TABLE {OTHER_DB}.{table} LIKE adam.{table}")
        with closing(db.connect()) as conn, closing(conn.cursor()) as cur:  # 另一條 root 連線：預設的資料庫換成新的
            cur.execute(f"USE {OTHER_DB}")
            for procedure in procedures:
                if procedure not in GONE:
                    cur.execute(db.query(f"SHOW CREATE PROCEDURE adam.{procedure}")[0][2])
        db.query(f"CREATE USER '{NARROW_USER}'@'%' IDENTIFIED BY '{PASSWORD}'")
        db.query(f"CREATE USER '{WIDE_USER}'@'%' IDENTIFIED BY '{PASSWORD}'")
        db.query(f"GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON {OTHER_DB}.* TO '{WIDE_USER}'@'%'")
        failed = []
        for grant in grants:
            try:
                db.query(grant.replace(" adam.", f" {OTHER_DB}.").replace("'adam_analysis'@", f"'{NARROW_USER}'@"))
            except mysql.connector.Error as exc:
                failed.append(exc.errno)
        yield sorted(failed)
    finally:
        drop()


# ---------- 照 grants.sql 給的權限：每一項都 OK、什麼都沒寫 ----------
def test_the_account_from_grants_sql_has_everything_and_nothing_is_written(db, capsys):
    before = snapshot(db, "adam")
    code, lines = check(capsys)
    needs = aq.read_needs()
    assert code == 0 and lines[:-1] == [f"OK      {need}" for need in needs]
    assert lines[-1] == SUMMARY.format("ok", 0, len(needs), 0, 0, "adam_analysis@%", "adam")
    assert snapshot(db, "adam") == before  # 沒有寫入：資料、AUTO_INCREMENT、binlog 的位置都沒變


# ---------- 收回權限：DENIED ----------
def test_a_revoked_column_privilege_and_execute_are_denied(db, capsys, undo):
    undo("REVOKE UPDATE (service_status) ON adam.tbl_service FROM 'adam_analysis'@'%'",
         "GRANT UPDATE (service_status) ON adam.tbl_service TO 'adam_analysis'@'%'")
    undo("REVOKE EXECUTE ON PROCEDURE adam.sp_insert_text_storage FROM 'adam_analysis'@'%'",
         "GRANT EXECUTE ON PROCEDURE adam.sp_insert_text_storage TO 'adam_analysis'@'%'")
    code, lines = check(capsys)
    problems = sorted(line for line in lines[:-1] if not line.startswith("OK "))
    assert code == 1 and len(problems) == 2
    # 欄位：這個帳號還看得到 service_status（有它的 SELECT），所以確定是在、但沒有權限
    assert re.fullmatch(r"DENIED  UPDATE \(service_status\) ON tbl_service: 1143 UPDATE command denied to user "
                        r"'adam_analysis'@'[^']+' for column 'service_status' in table 'tbl_service'", problems[1])
    # 程序：收回之後這個帳號看不到它，MySQL 不說它在不在
    assert problems[0] == ("DENIED  EXECUTE ON PROCEDURE sp_insert_text_storage: 1370 execute command denied to user "
                           "'adam_analysis'@'%' for routine 'adam.sp_insert_text_storage'" + aq.INVISIBLE_HINT)
    assert lines[-1] == SUMMARY.format("failed", 1, len(lines) - 3, 0, 2, "adam_analysis@%", "adam")
    undo.now()
    assert check(capsys)[0] == 0  # 給回去之後又全部 OK


# ---------- 改名（被刪掉、改掉）：最小權限的帳號也看得到 MISSING ----------
def test_a_renamed_column_and_table_are_missing_for_the_minimal_account_and_root(db, capsys, monkeypatch, undo):
    undo("ALTER TABLE tbl_report RENAME COLUMN ai_prompt_version TO ai_prompt_version_gone",
         "ALTER TABLE tbl_report RENAME COLUMN ai_prompt_version_gone TO ai_prompt_version")
    undo("RENAME TABLE tbl_analysis_worker TO tbl_analysis_worker_gone",
         "RENAME TABLE tbl_analysis_worker_gone TO tbl_analysis_worker")
    missing = ["MISSING UPDATE (ai_prompt_version) ON tbl_report: 1054 Unknown column 'ai_prompt_version' in 'field list'"]
    missing += [f"MISSING {privilege} ON tbl_analysis_worker: 1146 Table 'adam.tbl_analysis_worker' doesn't exist"
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")]
    # 舊的欄位、表權限還在（MySQL 改名、刪除時不收回），所以 adam_analysis 拿到的也是 1054、1146，不是沒有權限
    for user, password in [("adam_analysis", "adam-test-only"), ("root", os.environ["DB_ROOT_PASSWORD"])]:
        use_account(monkeypatch, user, password)
        code, lines = check(capsys)
        assert code == 1 and [line for line in lines[:-1] if not line.startswith("OK ")] == missing
        assert lines[-1] == SUMMARY.format("failed", 1, len(lines) - 6, 5, 0, f"{user}@%", "adam")
    undo.now()
    assert check(capsys)[0] == 0


# ---------- migration 還沒套用：root、整個資料庫的權限看到 MISSING，最小權限的帳號看到 DENIED ----------
def test_without_the_migration_root_sees_missing_and_the_minimal_account_denied(db, capsys, monkeypatch, other_db):
    assert other_db == [1146, 1305]  # 沒有的表、程序：它們的 GRANT 套用不了
    needs = aq.read_needs()
    for user, password, status, errors in [
            (NARROW_USER, PASSWORD, "DENIED", {"tbl_analysis_worker": 1142, "sp_insert_text_storage": 1370}),
            ("root", os.environ["DB_ROOT_PASSWORD"], "MISSING", {"tbl_analysis_worker": 1146, "sp_insert_text_storage": 1305}),
            (WIDE_USER, PASSWORD, "MISSING", {"tbl_analysis_worker": 1146, "sp_insert_text_storage": 1305})]:
        use_account(monkeypatch, user, password, OTHER_DB)
        before = snapshot(db, OTHER_DB)
        code, lines = check(capsys)
        assert code == 1 and items(lines) == [(status if need.name in GONE else "OK", str(need)) for need in needs]
        gone = [line for line in lines[:-1] if not line.startswith("OK ")]
        assert len(gone) == 5
        for line in gone:
            item, error = line.split(": ", 1)
            assert error.startswith(f"{errors[item.split()[-1]]} ")
            # 最小權限的帳號看不到它們：那幾行提醒「也可能不存在」；看得到全部的帳號直接說不存在
            assert line.endswith(aq.INVISIBLE_HINT) == (status == "DENIED")
        assert lines[-1] == SUMMARY.format("failed", 1, len(needs) - 5, 5 * (status == "MISSING"),
                                           5 * (status == "DENIED"), f"{user}@%", OTHER_DB)
        assert snapshot(db, OTHER_DB) == before


# ---------- 萬一參數個數猜對了：唯讀的 session 讓程序寫不進去 ----------
def test_a_call_that_would_run_cannot_write_in_the_read_only_session(db, capsys, monkeypatch, tmp_path, undo):
    undo("CREATE PROCEDURE check_db_writes() INSERT INTO tbl_analysis_worker (worker_id, worker_state, started_at, seen_at) "
         "VALUES ('check-db', 'ready', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))", "DROP PROCEDURE check_db_writes")
    db.query("GRANT EXECUTE ON PROCEDURE adam.check_db_writes TO 'adam_analysis'@'%'")  # DROP PROCEDURE 會一起收回
    grants = tmp_path / "grants.sql"
    grants.write_text("GRANT EXECUTE ON PROCEDURE adam.check_db_writes TO 'adam_analysis'@'%';\n", encoding="utf-8")
    # 故意讓 check-db 以為程序有 -1 個參數：CALL 的參數個數剛好對，程序真的執行
    monkeypatch.setattr(aq, "VISIBLE_ROUTINES_SQL", "SELECT 'check_db_writes', -1")
    code, lines = check(capsys, "--grants", str(grants))
    assert code == 2 and lines == [
        "check-db: could not check EXECUTE ON PROCEDURE check_db_writes: "
        "1792 Cannot execute statement in a READ ONLY transaction.",
        f"check-db: result=error code=2 ok=0 missing=0 denied=0 account=adam_analysis@% database=adam needs={grants}"]
    assert db.worker("check-db") is None  # 沒有寫進去


# ---------- 連不上：結束碼 2 ----------
@pytest.mark.parametrize("variable, value, error", [
    ("DB_PORT", "1", "2003 Can't connect to MySQL server on "),
    ("DB_PASSWORD", "not-the-password", "1045 Access denied for user 'adam_analysis'@"),
    ("DB_NAME", "adam_nowhere", "1044 Access denied for user 'adam_analysis'@'%' to database 'adam_nowhere'"),
    ("DB_TIME_ZONE", "Mars/Phobos", "1298 Unknown or incorrect time zone: 'Mars/Phobos'"),  # 和服務一樣設時區
])
def test_check_db_that_cannot_connect_exits_2(capsys, monkeypatch, variable, value, error):
    monkeypatch.setenv(variable, value)
    code, lines = check(capsys)
    assert code == 2 and len(lines) == 2 and lines[0].startswith(f"check-db: cannot connect to the database: {error}")
    assert lines[1] == SUMMARY.format("error", 2, 0, 0, 0, "-", "-")
