# =============================================================================
# adam-db-migrations：資料庫 migration 的 runner（CD 計畫第 9 節、CONTRACTS 第 2 節）
#   docker build -t adam-db-migrations services/db
#   docker run --rm -e DB_HOST -e DB_PASSWORD [-e DB_PORT -e DB_NAME -e DB_USER -e DB_SSL_MODE -e DB_SSL_CA] \
#     adam-db-migrations status | apply [--allow-destructive] | lint
# 基底是 mysql:8.4：只用它的 mysql 客戶端（有 DELIMITER 的 migration 要用 mysql 客戶端跑），不啟動伺服器。
# 只放客戶端要用的：runner、詞法檢查、基準檢查、migrations/、RDS 的 CA（certs/，放在 repo 裡的理由見 README）。
# OCI 的 label 由 workflow 加；MIGRATE_SOURCE_COMMIT 由 workflow 傳 --build-arg SOURCE_COMMIT=<commit>，記進 tbl_schema_migration。
# =============================================================================
FROM mysql:8.4

COPY migrate.sh /opt/adam-db/migrate.sh
COPY lib/sqlscan.awk /opt/adam-db/lib/sqlscan.awk
COPY baseline/check_001_007.sql /opt/adam-db/baseline/check_001_007.sql
COPY certs/rds-global-bundle.pem /opt/adam-db/certs/rds-global-bundle.pem
COPY migrations/ /opt/adam-db/migrations/
# 檔案屬於 root、所有人唯讀（不管 checkout 時的 umask）；runner 本身可以執行
RUN chmod -R u=rwX,go=rX /opt/adam-db && chmod 0755 /opt/adam-db/migrate.sh

# 放在最後：每個 commit 不一樣，放前面會讓上面幾層的快取失效
ARG SOURCE_COMMIT=
ENV MIGRATE_SOURCE_COMMIT=${SOURCE_COMMIT} \
    LC_ALL=C

# mysql 映像檔裡現成的非 root 帳號（uid 999）；runner 只需要讀上面的檔案、寫 /tmp
USER mysql:mysql
WORKDIR /tmp
ENTRYPOINT ["/opt/adam-db/migrate.sh"]
CMD ["status"]
