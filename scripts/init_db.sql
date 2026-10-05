-- scripts/init_db.sql
-- MySQL 首次启动自动执行：建三个业务库 + 授权给应用账号 office
-- 说明：office 账号与 MYSQL_DATABASE(office_app) 由容器环境变量创建；
--       本脚本补建另外两个库并授权。

CREATE DATABASE IF NOT EXISTS office_oa    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS office_asset CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS office_app   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON office_oa.*    TO 'office'@'%';
GRANT ALL PRIVILEGES ON office_asset.* TO 'office'@'%';
GRANT ALL PRIVILEGES ON office_app.*   TO 'office'@'%';
FLUSH PRIVILEGES;
