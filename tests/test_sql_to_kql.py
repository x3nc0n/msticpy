# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
from collections import namedtuple
from pathlib import Path
import os
import unittest

import pytest

from ..msticpy.dataproc import sql_to_kql


SQLTestCase = namedtuple("SQLTestCase", "sql, kql, id, rename")
SQL_CASES = [
    SQLTestCase(
        sql="""
        SELECT DISTINCT Message, Otherfield
        FROM apt29Host
        WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
            AND EventID BETWEEN 1 AND 10
            AND LOWER(ParentImage) LIKE '%explorer.exe'
            AND EventID IN ('4', '5', '6')
            AND LOWER(Image) LIKE "3aka3%"
        LIMIT 10
        """,
        kql="""
        SecurityEvent
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID between (1 .. 10)
        and tolower(ParentImage) endswith 'explorer.exe'
        and EventID in ('4', '5', '6')
        and tolower(Image) startswith '3aka3'
        | project Message, Otherfield
        | distinct *
        | limit 10
        """,
        id="select",
        rename={"apt29Host": "SecurityEvent"},
    ),
    SQLTestCase(
        sql="""
        SELECT DISTINCT Message, Otherfield, COUNT(DISTINCT EventID)
        FROM (SELECT EventID, ParentImage, Image, Message, Otherfield FROM apt29Host) as A
        --FROM A
        INNER JOIN (Select Message, foo FROM MyTable ) on MyTable.Message == A.Message and MyTable.foo == A.EventID
        WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
            AND EventID = 1
            AND LOWER(ParentImage) LIKE "%explorer.exe"
            AND LOWER(Image) RLIKE ".*3aka3%"
        GROUP BY EventID
        ORDER BY Message DESC, Otherfield
        LIMIT 10
        """,
        kql="""
        apt29Host
        | project EventID, ParentImage, Image, Message, Otherfield
        | join kind=inner (MyTable
        | project Message, foo) on $right.Message == $left.Message
        and $right.foo == $left.EventID
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID == 1
        and tolower(ParentImage) endswith 'explorer.exe'
        and tolower(Image) startswith '.*3aka3'
        | summarize any(Message), any(Otherfield), dcount(EventID) by EventID
        | order by Message desc, Otherfield
        | limit 10
        """,
        id="join",
        rename=None,
    ),
    SQLTestCase(
        sql="""
        SELECT Message
        FROM apt29Host a
        INNER JOIN (
            SELECT ProcessGuid
            FROM apt29Host
            WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
                AND EventID = 1
                AND LOWER(ParentImage) RLIKE '.*\\â€Ž|â€|â€ª|â€«|â€¬|â€|â€®.*'
                AND LOWER(Image) LIKE '%cmd.exe'
        ) b
        ON a.ParentProcessGuid = b.ProcessGuid
        WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
            AND EventID = 1
            AND LOWER(Image) LIKE '%powershell.exe'
        """,
        kql="""
        apt29Host
        | join kind=inner (apt29Host
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID == 1
        and tolower(ParentImage) matches regex '.*\â€Ž|â€|â€ª|â€«|â€¬|â€|â€®.*'
        and tolower(Image) endswith 'cmd.exe'
        | project ProcessGuid) on $left.ParentProcessGuid == $left.ProcessGuid
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID == 1
        and tolower(Image) endswith 'powershell.exe'
        | project Message
        """,
        id="join2",
        rename=None,
    ),
    SQLTestCase(
        sql="""
        SELECT DISTINCT Message, COUNT(Otherfield)
        FROM (SELECT *
            FROM (SELECT EventID, ParentImage, Image, Message, Otherfield FROM apt29Host)

            UNION
            SELECT DISTINCT Message, Otherfield, EventID
            FROM (SELECT EventID, ParentImage, Image, Message, Otherfield FROM apt29Host) as A
            INNER JOIN MyTable on MyTable.mssg = A.Message
            WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
                AND EventID = 1
                AND LOWER(ParentImage) LIKE "%explorer.exe"
                AND LOWER(Image) RLIKE ".*3aka3%"
                LIMIT 10
            )
        GROUP BY Message
        ORDER BY Message DESC, Otherfield
        """,
        kql="""
        apt29Host
        | project EventID, ParentImage, Image, Message, Otherfield
        | union (apt29Host
        | project EventID, ParentImage, Image, Message, Otherfield
        | join kind=inner (MyTable) on $right.mssg == $left.Message
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID == 1
        and tolower(ParentImage) endswith 'explorer.exe'
        and tolower(Image) startswith '.*3aka3'
        | project Message, Otherfield, EventID
        | distinct *
        | limit 10
        )
        | distinct *
        | summarize any(Message), count(Otherfield) by Message
        | order by Message desc, Otherfield
        """,
        id="union_groupby",
        rename=None,
    ),
    SQLTestCase(
        sql="""
        SELECT DISTINCT Message as mssg, COUNT(Otherfield)
        FROM (SELECT EventID as ID, ParentImage, Image, Message,
            ParentImage + Message as ParentMessage,
            LOWER(Otherfield) FROM apt29Host
            )
        WHERE Channel = "Microsoft-Windows-Sysmon/Operational"
            AND EventID = 1
            AND LOWER(ParentImage) LIKE "%explorer.exe"
        """,
        kql="""
        apt29Host
        | extend ParentMessage = ParentImage + Message, Otherfield = tolower(Otherfield)
        | project ID = EventID, ParentImage, Image, Message, ParentMessage, Otherfield
        | where Channel == 'Microsoft-Windows-Sysmon/Operational'
        and EventID == 1
        and tolower(ParentImage) endswith 'explorer.exe'
        | extend Otherfield = count(Otherfield)
        | project Message, Otherfield
        | distinct *
        """,
        id="select_rename",
        rename=None,
    ),
]


@pytest.fixture(params=SQL_CASES, ids=lambda t: t[2])
def get_sql_cases(request):
    """Pytest fixture for parameterized tests."""
    return request.param


def test_sql_convert(get_sql_cases):
    """Test SQL to KQL Conversion."""
    sql, kql, id, rename = get_sql_cases
    if rename:
        kql_query = sql_to_kql.sql_to_kql(sql, target_tables=rename)
    else:
        kql_query = sql_to_kql.sql_to_kql(sql)
    for line_test, line_expected in zip(
        kql_query.strip().split("\n"), kql.strip().split("\n")
    ):
        assert line_test.strip() == line_expected.strip()