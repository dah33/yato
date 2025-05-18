import logging
import os
import re
import tempfile
from graphlib import TopologicalSorter

import duckdb
from rich.console import Console

from yato.mermaid import generate_mermaid_diagram
from yato.parser import get_dependencies, is_select_tree, parse_sql, read_and_get_python_instance, read_sql

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.CRITICAL)


class RunContext:
    def __init__(self, con, fail_silently=False):
        """
        This class is a wrapper around the DuckDB connection object.
        :param con: DuckDB connection object.
        :param fail_silently: If True, it will not raise an error if an environment variable is not set. Default is False.
        """
        self.con = con
        self.fail_silently = fail_silently
        self.console = Console()

    def replace_env_vars(self, sql) -> str:
        """
        Replace the environment variables in the SQL.
        :param sql: The SQL to replace the environment variables in.
        :return: SQL with the environment variables replaced.
        """
        pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

        def replace_match(match):
            var_name = match.group(1)
            if not self.fail_silently and os.getenv(var_name) is None:
                raise ValueError(f"Environment variable {var_name} is not set.")
            return os.getenv(var_name)

        return pattern.sub(replace_match, sql)

    def sql(self, sql):
        self.con.sql(self.replace_env_vars(sql))


class Yato:
    def __init__(
        self,
        database_path: str,
        sql_folder: str,
        dialect: str = "duckdb",
        schema: str = "transform",
        db_folder_name: str = "db",
    ) -> None:
        """
        yato stands for yet another transformation orchestrator.

        The goal of yato is to provide the lightest SQL orchestrator on earth on top of DuckDB.
        You just need a bunch of SQL files in a folder, a configuration file, and you're good to go.

        :param database_path:  The path to the database file (reminder: only DuckDB is supported).
        :param sql_folder: The folder containing the SQL files to run.
        :param dialect: The SQL dialect to use in SQLGlot. Default is DuckDB and only DuckDB is supported.
        :param schema: The schema to use in the DuckDB database.
        :param db_folder_name: The name of the folder to use for backup and restore.
        """
        self.database_path = database_path
        self.sql_folder = sql_folder
        self.dialect = dialect
        self.schema = schema
        self.db_folder_name = db_folder_name

    def get_execution_order(self, dependencies):
        ts = TopologicalSorter({d: dependencies[d].deps for d in dependencies})
        return list(ts.static_order())

    def run_pre_queries(self, context: RunContext) -> None:
        context.sql(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
        context.sql(f"USE {self.schema}")

    def run_objects(self, execution_order, dependencies, context: RunContext) -> None:
        context.console.print(f"Running {len(execution_order)} objects...")
        with context.console.status("[bold green]Running...", speed=0.6) as status:
            for object_name in execution_order:
                if object_name not in dependencies:
                    context.console.print(f"[green]•[/] Identified {object_name} as a source.")
                    continue

                filename = dependencies[object_name].filename
                if os.path.exists(filename) and filename.endswith(".sql"):
                    status.update(f"[bold green]Running SQL {object_name}...")
                    try:
                        self.run_sql_query(filename, object_name, context)
                        context.console.print(f"[green]•[/] {object_name} completed.")
                    except Exception as e:
                        context.console.print(f"[red]Error running {object_name}: {e}[/]")
                elif os.path.exists(filename) and filename.endswith(".py"):
                    status.update(f"[bold green]Running Python {object_name}...")
                    self.run_python_query(filename, object_name, context)
                    context.console.print(f"[green]•[/] {object_name} completed.")
                else:
                    context.console.print(f"Identified {object_name} as a source.")

    def run_sql_query(self, filename, table_name, context: RunContext) -> None:
        """
        Runs a SQL query and creates a table in the DuckDB database.
        :param filename: Name of the SQL file to run.
        :param table_name: Name of the table to create.
        :param context: RunContext object.
        """
        sql = read_sql(filename)
        trees = parse_sql(sql, dialect=self.dialect)
        if len(trees) > 1:
            for tree in trees:
                if is_select_tree(tree):
                    context.sql(f"""CREATE OR REPLACE TABLE {self.schema}.{table_name} AS {tree}""")
                else:
                    context.sql(f"{tree}")
        else:
            context.sql(f"""CREATE OR REPLACE TABLE {self.schema}.{table_name} AS {sql}""")

    def run_python_query(self, filename, table_name, context: RunContext) -> None:
        """
        Runs a Python file and creates a table in the DuckDB database.
        :param filename: Name of the Python file to run.
        :param table_name: Name of the table to create.
        :param context: RunContext object.
        """
        instance = read_and_get_python_instance(filename)
        context.con.register("df", instance.run(context))
        context.sql(f"""CREATE OR REPLACE TABLE {self.schema}.{table_name} AS SELECT * FROM df""")

    def run(self) -> object:
        """
        Runs do all the magic, it parses all the SQL queries, resolves the dependencies,
        and runs the queries in the guessed order.

        :return: Then it returns a DuckDB connection object.
        """
        con = duckdb.connect(self.database_path)
        context = RunContext(con)
        dependencies = get_dependencies(self.sql_folder, self.dialect)
        execution_order = self.get_execution_order(dependencies)
        self.run_pre_queries(context)
        self.run_objects(execution_order, dependencies, context)
        generate_mermaid_diagram(self.sql_folder, dependencies)
        return con, context
