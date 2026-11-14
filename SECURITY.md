# Security policy

## Reporting a vulnerability

Please **do not** report a security problem in a public issue.

Report it privately on GitHub instead:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Describe the problem, how to reproduce it, and what an attacker could do with it.

Only the owner can see the report. You will get an answer, and the fix is
made in private before anything is published.

## What counts

Examples of what to report:

- input (a GPS point, code, polygon, GeoJSON, track, GPX file or stored file)
  that makes pakgrid crash, hang, use unbounded memory, or read or write files
  it should not;
- a way past the walkthrough Django app's permission rules (seeing,
  approving or deleting a walkthrough you should not);
- SQL injection, path traversal or command injection anywhere in the library
  or its command-line tools.

A wrong answer that is not a security problem (a point placed in the wrong
cell, for example) is a normal bug: use a public bug report.

## Supported versions

pakgrid has not had its first release yet. Fixes go into the latest code on
`main`.
