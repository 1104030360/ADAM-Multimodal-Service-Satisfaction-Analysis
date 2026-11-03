# =============================================================================
# sqlscan.awk：MySQL 的 SQL 檔案的簡單詞法處理。migrate.sh（lint、status、apply 的破壞性檢查）和
# tools/verify.sh（結構比對時正規化程序內容）共用，在 mysql:8.4 映像檔的 gawk 跑；只用 POSIX awk 的功能。
# 呼叫端一律設 LC_ALL=C（以位元組為單位處理，中文註解、字串不影響）。
#
# 用法：
#   awk -v mode=check  -f sqlscan.awk 檔案   破壞性的語句，每一項一行：<行號><TAB><原因><TAB><語句的開頭>。
#                                            檔案有 `-- migrate: destructive` 這一行時，多一行 <行號><TAB>declared<TAB>…。
#                                            沒有輸出＝不是破壞性的。
#   awk -v mode=pieces -f sqlscan.awk 檔案   除錯用：每個語句片段一行 <行號><TAB><深度><TAB><正規化的內容>。
#   awk -v mode=dump   -f sqlscan.awk        tools/schema_dump.sql 的輸出：@@BEGIN <標頭> 到 @@END 之間（程序、觸發程序的
#                                            內容）去掉註解、壓縮空白，變成一行 <標頭>|<內容>；其他行原樣輸出。
#
# 詞法（和 mysql 客戶端一樣）：'…'、"…" 是字串（\ 跳脫、兩個引號代表一個引號）；`…` 是名稱；
#   -- 接空白、# 到行尾、/* … */ 是註解；/*! … */ 的內容照樣當成 SQL（MySQL 會執行）；
#   DELIMITER 換分隔符號（只認檔案最外層的行首）。
# 破壞性檢查用的「語句片段」：; 和目前的分隔符號都切開（程序內容也一句一句看）；註解拿掉、字串換成 ''、其他轉大寫。
#   字串的內容開頭是 SQL 的關鍵字時（005 那種 SET @step := '…ALTER TABLE…' 再 PREPARE 的動態 SQL），內容也切開檢查。
# 規則寫在 services/db/README.md 的「破壞性的語句」。
# =============================================================================

BEGIN {
    if (mode == "") mode = "check"
    np = 0; nev = 0; declared = 0; text = ""; inbody = 0
}

# ---- dump：程序內容正規化 -----------------------------------------------------------
mode == "dump" {
    if (inbody) {
        if ($0 == "@@END") { print bodyhead "|" norm(bodytext); inbody = 0; next }
        bodytext = bodytext "\n" $0
        next
    }
    if (substr($0, 1, 8) == "@@BEGIN ") { inbody = 1; bodyhead = substr($0, 9); bodytext = ""; next }
    print
    next
}

# ---- check、pieces：整個檔案讀進來 ---------------------------------------------------
{
    if (declared == 0 && tolower($0) ~ /^[ \t]*--[ \t]*migrate:[ \t]*destructive([^a-z_]|$)/) declared = NR
    text = text $0 "\n"
}

END {
    if (mode == "dump") {
        if (inbody) print bodyhead "|" norm(bodytext)
        exit 0
    }
    lex(text, 1, 0)
    if (mode == "pieces") {
        for (i = 1; i <= np; i++) print pline[i] "\t" pdepth[i] "\t" ptext[i]
        exit 0
    }
    check_all()
    if (declared) printf "%d\tdeclared\t檔案標了 -- migrate: destructive（一律當成破壞性的）\n", declared
    exit 0
}

# =============================================================================
# 詞法：把 text 切成語句片段（emit）。depth 0 是檔案本身；字串裡的動態 SQL 用 depth+1 再切（最多到 2）。
# 片段的狀態放在以 depth 為索引的全域陣列：B（內容）、SL（開始的行號）、SS（SL 設了沒有）、NS（待處理的字串數）。
# =============================================================================
function lex(t, line0, depth,    n, i, c, c2, c3, st, line, delim, dlen, atbol, rest, q, execc, s, sline_s, m) {
    n = length(t); i = 1; st = 0; line = line0; delim = ";"; dlen = 1; atbol = 1; execc = 0
    B[depth] = ""; SL[depth] = line0; SS[depth] = 0; NS[depth] = 0
    while (i <= n) {
        c = substr(t, i, 1)
        if (st == 0) {
            if (depth == 0 && atbol) {
                rest = substr(t, i, 256)
                if (match(rest, /^[ \t]*[Dd][Ee][Ll][Ii][Mm][Ii][Tt][Ee][Rr][ \t]+[^ \t\r\n]+/)) {
                    m = RLENGTH
                    q = substr(rest, 1, m)
                    sub(/^[ \t]*[Dd][Ee][Ll][Ii][Mm][Ii][Tt][Ee][Rr][ \t]+/, "", q)
                    endpiece(depth)
                    delim = q; dlen = length(q)
                    i += m
                    while (i <= n && substr(t, i, 1) != "\n") i++
                    continue
                }
            }
            atbol = 0
            if (c == "\n") { line++; B[depth] = B[depth] " "; atbol = 1; i++; continue }
            if (delim != ";" && substr(t, i, dlen) == delim) { endpiece(depth); SL[depth] = line; i += dlen; continue }
            if (c == ";") { endpiece(depth); SL[depth] = line; i++; continue }
            if (c == "'" || c == "\"") {
                st = (c == "'") ? 1 : 2; s = ""; sline_s = line
                addcode(depth, " '' ", line)
                i++; continue
            }
            if (c == "`") { st = 3; i++; continue }
            if (c == "#") { st = 4; i++; continue }
            c2 = substr(t, i + 1, 1)
            c3 = substr(t, i + 2, 1)
            if (c == "-" && c2 == "-" && (c3 == "" || c3 ~ /[[:space:][:cntrl:]]/)) { st = 4; i += 2; continue }
            if (c == "/" && c2 == "*") {
                if (c3 == "!") {
                    # /*!80000 … */：版本號之後的內容照樣是 SQL
                    i += 3
                    while (i <= n && substr(t, i, 1) ~ /[0-9]/) i++
                    execc++
                    continue
                }
                st = 5; i += 2; continue
            }
            if (c == "*" && c2 == "/" && execc > 0) { execc--; i += 2; continue }
            addcode(depth, toupper(c), line)
            i++; continue
        }
        if (st == 1 || st == 2) {
            q = (st == 1) ? "'" : "\""
            if (c == "\\") {
                c2 = substr(t, i + 1, 1)
                if (c2 == "\n") line++
                s = s c2; i += 2; continue
            }
            if (c == q) {
                if (substr(t, i + 1, 1) == q) { s = s q; i += 2; continue }
                st = 0
                NS[depth]++; STR[depth, NS[depth]] = s; STRL[depth, NS[depth]] = sline_s
                i++; continue
            }
            if (c == "\n") line++
            s = s c; i++; continue
        }
        if (st == 3) {
            if (c == "`") {
                if (substr(t, i + 1, 1) == "`") { addcode(depth, "`", line); i += 2; continue }
                st = 0; i++; continue
            }
            if (c == "\n") line++
            addcode(depth, toupper(c), line)
            i++; continue
        }
        if (st == 4) {
            if (c == "\n") { st = 0; continue }
            i++; continue
        }
        if (st == 5) {
            if (c == "*" && substr(t, i + 1, 1) == "/") { st = 0; B[depth] = B[depth] " "; i += 2; continue }
            if (c == "\n") line++
            i++; continue
        }
    }
    endpiece(depth)
}

function addcode(depth, s, line) {
    if (!SS[depth] && s !~ /^[[:space:]]*$/) { SL[depth] = line; SS[depth] = 1 }
    B[depth] = B[depth] s
}

function endpiece(depth,    code, k, ns) {
    code = B[depth]
    # 標點前後加空白，之後用空白切成詞（名稱裡的 . 和 @ 留著）
    gsub(/[(),=<>+*\/%!|&^~:]/, " & ", code)
    gsub(/[[:space:]]+/, " ", code)
    sub(/^ /, "", code); sub(/ $/, "", code)
    if (code != "") { np++; pline[np] = SL[depth]; ptext[np] = code; pdepth[np] = depth }
    B[depth] = ""; SS[depth] = 0
    ns = NS[depth]; NS[depth] = 0
    if (depth >= 2) return
    for (k = 1; k <= ns; k++)
        if (looks_sql(STR[depth, k])) lex(STR[depth, k], STRL[depth, k], depth + 1)
}

function looks_sql(s) {
    s = toupper(s)
    sub(/^[[:space:](]+/, "", s)
    return s ~ /^(ALTER|DROP|TRUNCATE|RENAME|DELETE|UPDATE|REPLACE|REVOKE|CREATE|GRANT|INSERT)([^A-Z0-9_$]|$)/
}

# =============================================================================
# 破壞性檢查
# =============================================================================
function flag(i, reason,    key, s) {
    key = i SUBSEP reason
    if (key in flagged) return
    flagged[key] = 1
    s = ptext[i]
    if (length(s) > 160) s = substr(s, 1, 157) "..."
    printf "%d\t%s\t%s\n", pline[i], reason, s
}

# 名稱去掉資料庫的前綴（ADAM.SP_X → SP_X）
function bare(name) {
    sub(/^.*\./, "", name)
    return name
}

function check_all(    i, n, w, j, k, a, nt, pv, name, kind, has_set, has_where, has_from) {
    for (i = 1; i <= np; i++) {
        n = split(ptext[i], w, " ")
        if (w[1] == "GRANT") { note_grant(i, w, n); continue }
        for (j = 1; j <= n; j++) consumed[j] = 0
        # ALTER TABLE 之後的 DROP、RENAME、MODIFY、CHANGE 等：刪欄位、改型別、改名
        for (j = 1; j <= n; j++) {
            if (w[j] != "ALTER") continue
            a = j + 1
            while (a <= n && (w[a] == "ONLINE" || w[a] == "OFFLINE" || w[a] == "IGNORE")) a++
            if (w[a] != "TABLE") continue
            for (k = a + 2; k <= n; k++) {
                consumed[k] = 1
                if (w[k] ~ /^(DROP|RENAME|MODIFY|CHANGE|CONVERT|DISCARD|TRUNCATE|REMOVE|COALESCE|REORGANIZE|EXCHANGE)$/)
                    flag(i, "ALTER TABLE " w[a + 1] " ... " w[k])
            }
        }
        for (j = 1; j <= n; j++) {
            if (consumed[j]) continue
            nt = (j < n) ? w[j + 1] : ""
            pv = (j > 1) ? w[j - 1] : ""
            if (w[j] == "DROP") {
                if (nt == "TEMPORARY" || nt == "PREPARE") continue
                if (nt == "PROCEDURE" || nt == "FUNCTION") {
                    k = j + 2
                    if (w[k] == "IF" && w[k + 1] == "EXISTS") k += 2
                    nev++; ev_kind[nev] = "drop"; ev_piece[nev] = i; ev_name[nev] = bare(w[k]); ev_type[nev] = nt
                    continue
                }
                flag(i, "DROP " nt)
            } else if (w[j] == "TRUNCATE") {
                if (nt != "(") flag(i, "TRUNCATE")
            } else if (w[j] == "RENAME") {
                flag(i, "RENAME " nt)
            } else if (w[j] == "REVOKE") {
                flag(i, "REVOKE（拿掉權限）")
            } else if (w[j] == "REPLACE") {
                if (nt != "(" && pv != "OR") flag(i, "REPLACE（重複的列先刪掉）")
            } else if (w[j] == "UPDATE") {
                if (pv == "ON" || pv == "FOR" || pv == "KEY" || pv == "BEFORE" || pv == "AFTER") continue
                has_set = 0; has_where = 0
                for (k = j + 1; k <= n; k++) {
                    if (w[k] == "SET") has_set = 1
                    if (has_set && w[k] == "WHERE") has_where = 1
                }
                if (has_set && !has_where) flag(i, "UPDATE 沒有 WHERE")
            } else if (w[j] == "DELETE") {
                if (pv == "ON" || pv == "BEFORE" || pv == "AFTER") continue
                has_from = 0; has_where = 0
                for (k = j + 1; k <= n; k++) {
                    if (w[k] == "FROM") has_from = 1
                    if (has_from && w[k] == "WHERE") has_where = 1
                }
                if (has_from && !has_where) flag(i, "DELETE 沒有 WHERE")
            } else if (w[j] == "CREATE") {
                # CREATE [DEFINER = …] [AGGREGATE] PROCEDURE|FUNCTION [IF NOT EXISTS] 名稱
                for (k = j + 1; k <= n && k <= j + 8; k++) {
                    if (w[k] == "PROCEDURE" || w[k] == "FUNCTION") {
                        kind = w[k]; k++
                        if (w[k] == "IF" && w[k + 1] == "NOT" && w[k + 2] == "EXISTS") k += 3
                        nev++; ev_kind[nev] = "create"; ev_piece[nev] = i; ev_name[nev] = bare(w[k]); ev_type[nev] = kind
                        break
                    }
                    if (w[k] == "TABLE" || w[k] == "VIEW" || w[k] == "INDEX" || w[k] == "TRIGGER" || w[k] == "EVENT") break
                }
            }
        }
    }
    check_routines()
}

# GRANT … EXECUTE … ON PROCEDURE|FUNCTION 名稱 TO …（ALL 也算）
function note_grant(i, w, n,    j, exec) {
    exec = 0
    for (j = 2; j <= n; j++) {
        if (w[j] == "EXECUTE" || w[j] == "ALL") exec = 1
        if (w[j] == "ON") break
    }
    if (!exec || j >= n) return
    if (w[j + 1] != "PROCEDURE" && w[j + 1] != "FUNCTION") return
    nev++; ev_kind[nev] = "grant"; ev_piece[nev] = i; ev_name[nev] = bare(w[j + 2]); ev_type[nev] = w[j + 1]
}

# DROP PROCEDURE／FUNCTION：MySQL 會連同所有帳號在它上面的 EXECUTE 一起拿掉（T42 第 9 節）。
#   - 同一個檔案先建立、最後又刪掉（暫時的輔助程序）：不算。
#   - 刪了沒有再建立：拿掉程序，舊版還在呼叫的話會壞 → 破壞性。
#   - 刪了再建立，但之後沒有 GRANT EXECUTE ON PROCEDURE 那個名稱：其他帳號的 EXECUTE 不見了 → 破壞性。
function check_routines(    e, f, nm, has_create, last, recreated, granted) {
    for (e = 1; e <= nev; e++) {
        if (ev_kind[e] != "drop") continue
        nm = ev_name[e]
        has_create = 0; last = ""
        for (f = 1; f <= nev; f++) {
            if (ev_name[f] != nm) continue
            if (ev_kind[f] == "create") has_create = 1
            if (ev_kind[f] == "create" || ev_kind[f] == "drop") last = ev_kind[f]
        }
        if (has_create && last == "drop") continue
        recreated = 0; granted = 0
        for (f = e + 1; f <= nev; f++) {
            if (ev_name[f] != nm) continue
            if (ev_kind[f] == "create") recreated = 1
            if (recreated && ev_kind[f] == "grant") granted = 1
        }
        if (!recreated)
            flag(ev_piece[e], "DROP " ev_type[e] " " nm "（拿掉之後沒有再建立：還在呼叫它的版本會壞）")
        else if (!granted)
            flag(ev_piece[e], "DROP " ev_type[e] " " nm "（重新建立了，但之後沒有 GRANT EXECUTE：其他帳號在它上面的 EXECUTE 會不見）")
    }
}

# =============================================================================
# dump：去掉註解、字串原樣保留、字串以外的連續空白換成一個空白
# =============================================================================
function norm(t,    n, i, c, c2, c3, st, out, q, pend) {
    n = length(t); i = 1; st = 0; out = ""; pend = 0
    while (i <= n) {
        c = substr(t, i, 1)
        if (st == 0) {
            if (c ~ /[[:space:]]/) { pend = 1; i++; continue }
            c2 = substr(t, i + 1, 1)
            c3 = substr(t, i + 2, 1)
            if (c == "#") { st = 4; i++; continue }
            if (c == "-" && c2 == "-" && (c3 == "" || c3 ~ /[[:space:][:cntrl:]]/)) { st = 4; i += 2; continue }
            if (c == "/" && c2 == "*" && c3 != "!") { st = 5; i += 2; continue }
            if (pend && out != "") out = out " "
            pend = 0
            if (c == "'" || c == "\"" || c == "`") { st = 1; q = c; out = out c; i++; continue }
            out = out c; i++; continue
        }
        if (st == 1) {
            out = out c
            if (c == "\\" && q != "`") { out = out substr(t, i + 1, 1); i += 2; continue }
            if (c == q) {
                if (substr(t, i + 1, 1) == q) { out = out q; i += 2; continue }
                st = 0
            }
            i++; continue
        }
        if (st == 4) {
            if (c == "\n") { st = 0; pend = 1 }
            i++; continue
        }
        if (st == 5) {
            if (c == "*" && substr(t, i + 1, 1) == "/") { st = 0; pend = 1; i += 2; continue }
            i++; continue
        }
    }
    return out
}
