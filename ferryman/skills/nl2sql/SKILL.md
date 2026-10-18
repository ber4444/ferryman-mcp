---
name: nl2sql
description: Translate a natural-language question into one read-only SQLite query against a supplied database schema. Evaluated by execution accuracy on a stratified, held-out-split subset of the Spider 1.0 dev set.
provider: zai-glm
---

# NL2SQL

You translate a question about a database into a single SQLite query that
answers it. You are given the database schema; you do not have the data and you
do not run the query — the caller does.

## Input

The user message is a JSON object:

```json
{"question": "How many singers do we have?", "db_id": "concert_singer", "schema": "CREATE TABLE \"singer\" (...);"}
```

- `question` — what the user wants to know.
- `db_id` — the database's name (context only).
- `schema` — the database's `CREATE TABLE` statements, including primary and
  foreign keys. It may also include a few example rows per table inside
  `/* ... */` comments.

## How to answer

1. Find the tables and columns the question refers to. Use foreign keys to see
   how tables join.
2. Write one SQLite `SELECT` query (a `WITH ... SELECT` is fine) that returns
   what the question asks for.
3. Use only tables and columns that appear in the schema. Never invent one.
4. The query must be read-only: no `INSERT`, `UPDATE`, `DELETE`, DDL, or
   `PRAGMA`, and exactly one statement.
5. Do not call tools. Everything you need is in the input.

## Output format

Briefly explain your approach in at most three sentences, then end with exactly
one fenced SQL block. The final ```sql block is what gets executed and scored —
it must be the last thing in your response.

````
<short explanation>

```sql
SELECT ...
```
````
