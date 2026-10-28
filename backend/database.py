from sqlmodel import Field, SQLModel, create_engine

DATABASE_URL = "sqlite:///trade_office.db"
engine = create_engine(DATABASE_URL)


class Client(SQLModel, table=True):
    """One tradesperson using Trade Office."""

    id: int | None = Field(default=None, primary_key=True)
    business_name: str
    owner_mobile: str
    twilio_number: str = Field(unique=True, index=True)
    text_back_message: str
    active: bool = True


def create_tables():
    """Create any tables that don't exist yet."""
    SQLModel.metadata.create_all(engine)
