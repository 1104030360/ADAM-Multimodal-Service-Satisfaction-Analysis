-- 測試專用：跑很久的 migration（測中斷）。
SET NAMES utf8mb4;
USE adam;

DO SLEEP(20);
CREATE TABLE IF NOT EXISTS tbl_test_slow (id INT NOT NULL PRIMARY KEY);
