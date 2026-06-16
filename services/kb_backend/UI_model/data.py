import datetime
import pathlib
from operator import truediv
from typing import List, Dict, Set, Tuple, Literal, Annotated, Union
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
import uuid
from .enum_type import *

# Model
class Document(BaseModel):
    source_type: Literal[SourceType.DOC]
    doc_type: str
    author: str | None
    published_date: datetime.datetime | None

class Image(BaseModel):
    source_type: Literal[SourceType.IMAGE]
    video_type: str
    height: int
    width: int
    color_space: str | None

class Video(BaseModel):
    source_type: Literal[SourceType.VIDEO]
    video_type: str
    height: int
    width: int
    codec: str | None
    total_frame: int

class Web(BaseModel):
    source_type: Literal[SourceType.WEB]
    url: str
    web_name:str

MetadataType = Annotated[
    Union[
        Document,
        Image,
        Video,
        Web,
    ],
    Field(discriminator = "source_type")
]

# Data API Model
# - Request
# POST /api/knowledge/data_upload
class RequestDataUpload(BaseModel):
    file_name: str
    source_type: SourceType

# POST /api/knowledge/confirm/:upload_id
class RequestConfirmDataUpload(BaseModel):
    upload_id: uuid.UUID
    upload_status: bool

# PATCH /api/data/documents/:doc_id
class RequestUpdateDocument(BaseModel):
    current_tier: Optional[str] = None  # bronze | silver | gold
    status: Optional[str] = None
    metadata: Optional[dict] = None

# - Response Model
# GET /api/knowledge/data
class DataConfigure(BaseModel):
    file_name: str
    upload_id: uuid.UUID
    source_type: SourceType
    metadata: MetadataType
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
    )