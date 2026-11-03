-- 測試專用（tests/run.sh 的 lint）：標了 destructive 的檔案，一律當成破壞性的
-- migrate: destructive 測試用，沒有破壞性的語句也一樣
CREATE TABLE IF NOT EXISTS tbl_declared (id INT NOT NULL PRIMARY KEY);
