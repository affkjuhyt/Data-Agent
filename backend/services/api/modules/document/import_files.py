"""Files put into a Data Space's document collection by the upload API's own code (EV-05).

The TLC data dictionaries (tests/e2e/tlc/sources.json, pinned by sha256) answer the golden set's
hybrid cases, yet their terms keep them out of the repo, so they cannot be a seed the way DS-AW's
corpus is. This runs where the api runs and calls the routes themselves, `create_collection` when
the collection's code is new and `upload_document` for each file, as the named user: the file is
checked by content, stored content-addressed in da-raw-docs and announced as document.received,
so worker-document reads, masks, chunks and indexes it exactly as an upload through the screen.
A file already in the collection (same sha256) is answered by the existing document.

    python -m services.api.modules.document.import_files FILE … --org <id> --space <code> \\
        --as <user> --collection <code> --name <name> --purpose <purpose>

(`make tlc-documents` copies the cached dictionaries into the api pod and runs it.)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from fastapi import Response, UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import document as doc
from libs.dataagent_db.models import org as org_models
from services.api.kernel.auth import Principal
from services.api.modules.document.ports import object_store
from services.api.modules.document.routes import ROLE, create_collection, upload_document
from services.api.modules.document.schemas import CollectionCreate


async def _principal(org_id: uuid.UUID, space_code: str, username: str) -> Principal:
    async with scoped_session(ROLE, org_id, None) as session:
        space = await session.scalar(
            select(org_models.DataSpace).where(
                org_models.DataSpace.org_id == org_id, org_models.DataSpace.code == space_code
            )
        )
        user = await session.scalar(
            select(org_models.AppUser).where(
                org_models.AppUser.org_id == org_id, org_models.AppUser.username == username
            )
        )
    if space is None or user is None:
        raise SystemExit(f"no Data Space {space_code} or user {username} in organisation {org_id}")
    return Principal(
        user_id=user.id, org_id=org_id, external_id=user.external_id or "", data_space_id=space.id
    )


async def _collection(principal: Principal, args: argparse.Namespace) -> uuid.UUID:
    assert principal.data_space_id is not None
    async with scoped_session(ROLE, principal.org_id, principal.user_id) as session:
        found = await session.scalar(
            select(doc.Collection.id).where(
                doc.Collection.org_id == principal.org_id,
                doc.Collection.data_space_id == principal.data_space_id,
                doc.Collection.code == args.collection,
                doc.Collection.deleted_at.is_(None),
            )
        )
    if found is not None:
        return found
    made = await create_collection(
        CollectionCreate(code=args.collection, name=args.name, purpose=args.purpose),
        principal,
        principal.data_space_id,
    )
    return made.id


async def run(args: argparse.Namespace) -> dict[str, Any]:
    principal = await _principal(uuid.UUID(args.org), args.space, args.username)
    assert principal.data_space_id is not None
    collection_id = await _collection(principal, args)
    store = object_store()
    out: dict[str, Any] = {"collection_id": str(collection_id), "documents": []}
    for name in args.files:
        path = Path(name)
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = await asyncio.to_thread(path.read_bytes)
        upload = UploadFile(
            file=io.BytesIO(data), filename=path.name, headers=Headers({"content-type": kind})
        )
        response = Response()
        accepted = await upload_document(
            upload, collection_id, principal, principal.data_space_id, response, store
        )
        out["documents"].append(
            {
                "file": path.name,
                "id": str(accepted.id),
                "status": accepted.status,
                # 200: the same file was already in the collection, and that document answers.
                "new": response.status_code != 200,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--org", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--as", dest="username", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--purpose", required=True)
    print(json.dumps(asyncio.run(run(parser.parse_args(argv))), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
