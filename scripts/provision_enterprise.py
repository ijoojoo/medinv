"""Open a local enterprise from an explicit manifest; never seeds inventory or passwords."""

import argparse
import json
import os
from pathlib import Path

from dev import environment
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork, check_runtime_role
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.provisioning import EnterprisePlan, EnterpriseProvisioner
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    plan = EnterprisePlan.model_validate_json(
        args.manifest.read_text(encoding="utf-8-sig")
    )
    os.environ.update(environment())
    config = Settings()
    business = create_engine(
        config.database_url.get_secret_value(), hide_parameters=True
    )
    control = create_engine(
        os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True
    )
    try:
        check_runtime_role(business, "med")
        result = EnterpriseProvisioner(control, TenantUnitOfWork(business, config)).run(
            plan
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        business.dispose()
        control.dispose()


if __name__ == "__main__":
    try:
        main()
    except BusinessError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from None
    except ValidationError:
        raise SystemExit("开通清单字段、角色或独立审核人员配置不正确") from None
    except SQLAlchemyError:
        raise SystemExit(
            "数据库开通未完成；核对服务与迁移后，使用原清单重试。"
        ) from None
