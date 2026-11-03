-- 測試專用：排在失敗的 013 後面，不應該被執行。
SET NAMES utf8mb4;
USE adam;

CREATE TABLE IF NOT EXISTS tbl_test_after_failure (id INT NOT NULL PRIMARY KEY);
