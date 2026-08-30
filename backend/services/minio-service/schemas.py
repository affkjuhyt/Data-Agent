from pydantic import BaseModel


class BucketCreate(BaseModel):
    name: str
