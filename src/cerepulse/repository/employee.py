"""Employee identity persistence.

The portal renders the signed-in employee's code and name into a banner on every page, so
this is populated as a side effect of any sync rather than needing a request of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from cerepulse.repository.database import Database


@dataclass(frozen=True, slots=True)
class Employee:
    """The signed-in employee."""

    code: str
    name: str = ""
    company_code: str = ""


class EmployeeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, employee: Employee) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO employee (code, name, company_code) VALUES (?, ?, ?)
                ON CONFLICT (code) DO UPDATE SET
                    -- Keep a known name if a later sync only recovered the code.
                    name         = CASE WHEN excluded.name = '' THEN employee.name
                                        ELSE excluded.name END,
                    company_code = CASE WHEN excluded.company_code = ''
                                        THEN employee.company_code
                                        ELSE excluded.company_code END
                """,
                (employee.code, employee.name, employee.company_code),
            )

    def find(self, code: str) -> Employee | None:
        row = self.database.execute("SELECT * FROM employee WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        return Employee(code=row["code"], name=row["name"], company_code=row["company_code"])

    def find_any(self) -> Employee | None:
        """The single cached employee, for restoring session context on launch."""
        row = self.database.execute("SELECT * FROM employee LIMIT 1").fetchone()
        if row is None:
            return None
        return Employee(code=row["code"], name=row["name"], company_code=row["company_code"])
