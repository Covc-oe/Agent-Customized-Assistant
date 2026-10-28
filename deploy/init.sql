-- ============================================================
-- 设备库初始化脚本
-- docker-entrypoint-initdb.d 会在容器首次启动时自动执行
-- （库名由 MYSQL_DATABASE 环境变量创建，此处直接建表）
--
-- 表结构取自生产库 SHOW CREATE TABLE devices;
-- 唯一差异：model 由 varchar(5) 放宽到 varchar(64)。
--   原长度下 "RoboVac X10 Pro" 这类型号在严格模式会写入失败（ERROR 1406），
--   非严格模式会被静默截断，查询结果不可信。
-- ============================================================

CREATE TABLE IF NOT EXISTS `devices`
(
    `device_sn`            varchar(20) NOT NULL COMMENT '设备序列号',
    `model`                varchar(64) DEFAULT NULL COMMENT '型号',
    `activation_date`      date        DEFAULT NULL COMMENT '激活日期',
    `warranty_expire_date` date        DEFAULT NULL COMMENT '保修截止日期',
    `status`               varchar(5)  DEFAULT '正常' COMMENT '运行状态',
    `battery_health`       decimal(5, 2) DEFAULT NULL COMMENT '电池健康度百分比',
    PRIMARY KEY (`device_sn`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci COMMENT ='设备表';

INSERT IGNORE INTO `devices` (`device_sn`, `model`, `activation_date`, `warranty_expire_date`, `status`,
                              `battery_health`)
VALUES ('SN1001', 'X10 Pro', '2025-01-10', '2027-01-10', '正常', 98.50),
       ('SN2002', 'X10', '2024-06-18', '2026-06-18', '维修中', 82.00),
       ('SN3003', 'S8', '2023-11-02', '2025-11-02', '已停用', 65.30);
