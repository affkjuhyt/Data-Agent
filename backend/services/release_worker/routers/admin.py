from typing import Any
from fastapi import APIRouter

from release_core import *
from schemas import *

router = APIRouter()


@router.post("/api/release/admin/create-partition")
def create_next_partition(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    Tạo partition release_history cho tháng tiếp theo.
    Thay thế cho pg_cron job — có thể gọi từ CI/CD đầu mỗi tháng.
    Idempotent: IF NOT EXISTS đảm bảo an toàn khi gọi nhiều lần.
    """
    if "platform-admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Only platform-admin can create partitions")

    from datetime import date

    today = date.today()
    if today.month == 12:
        ny, nm = today.year + 1, 1
    else:
        ny, nm = today.year, today.month + 1

    if nm == 12:
        ey, em = ny + 1, 1
    else:
        ey, em = ny, nm + 1

    start = f"{ny}-{nm:02d}-01"
    end = f"{ey}-{em:02d}-01"
    table_name = f"release_history_{ny}_{nm:02d}"

    try:
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""CREATE TABLE IF NOT EXISTS {table_name}
                        PARTITION OF release_history
                        FOR VALUES FROM ('{start}') TO ('{end}')"""
                )
        log.info("admin.partition.created", table=table_name, start=start, end=end,
                 created_by=user["user_id"])
        return {
            "status": "created",
            "table": table_name,
            "from": start,
            "to": end,
        }
    except Exception as e:
        log.error("admin.partition.error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
