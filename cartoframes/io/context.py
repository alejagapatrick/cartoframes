import time
import pandas as pd

from warnings import warn

from carto.auth import APIKeyAuthClient
from carto.exceptions import CartoRateLimitException
from carto.sql import SQLClient, BatchSQLClient, CopySQLClient

from ..auth.defaults import get_default_credentials

from ..utils.utils import is_sql_query, check_credentials, encode_row, PG_NULL
from ..utils.geom_utils import compute_query_from_table, decode_geometry
from ..utils.columns import Column, DataframeColumnsInfo, obtain_index_col, obtain_converters, \
                            date_columns_names, normalize_name

from .. import __version__

DEFAULT_RETRY_TIMES = 3


class ContextManager(object):

    def __init__(self, credentials):
        credentials = credentials or get_default_credentials()
        check_credentials(credentials)

        auth_client = APIKeyAuthClient(
            base_url=credentials.base_url,
            api_key=credentials.api_key,
            session=credentials.session,
            client_id='cartoframes_{}'.format(__version__),
            user_agent='cartoframes_{}'.format(__version__)
        )

        self.sql_client = SQLClient(auth_client)
        self.copy_client = CopySQLClient(auth_client)
        self.batch_sql_client = BatchSQLClient(auth_client)

    def execute_query(self, query, parse_json=True, do_post=True, format=None, **request_args):
        return self.sql_client.send(query.strip(), parse_json, do_post, format, **request_args)

    def execute_long_running_query(self, query):
        return self.batch_sql_client.create_and_wait_for_completion(query.strip())

    def copy_to(self, source, schema, limit=None, retry_times=DEFAULT_RETRY_TIMES, keep_the_geom_webmercator=False):
        query = self._compute_query(source, schema)
        self._check_exists(query)
        columns = self._get_columns(query)
        copy_query = self._get_copy_query(query, columns, limit, keep_the_geom_webmercator)
        return self._copy_to(copy_query, columns, retry_times)

    def copy_from(self, cdf, table_name, if_exists):
        dataframe_columns_info = DataframeColumnsInfo(cdf)
        schema = self.get_schema()
        table_name = self._normalize_table_name(table_name)

        if if_exists == 'replace' or not self.has_table(table_name):
            print('Debug: creating table')
            self._create_table(table_name, dataframe_columns_info.columns, schema)
        elif if_exists == 'fail':
            raise Exception('Table "{schema}.{table_name}" already exists in CARTO. '
                            'Please choose a different `table_name` or use '
                            'if_exists="replace" to overwrite it'.format(
                                table_name=table_name, schema=schema))
        elif if_exists == 'append':
            pass

        return self._copy_from(cdf, table_name, dataframe_columns_info)

    def has_table(self, table_name, schema):
        schema = schema or self.get_schema()
        query = compute_query_from_table(table_name, schema)
        try:
            self._check_exists(query)
            return True
        except Exception:
            return False

    def get_schema(self):
        """Get user schema from current credentials"""
        query = 'SELECT current_schema()'
        result = self.execute_query(query, do_post=False)
        return result['rows'][0]['current_schema']

    def _create_table(self, table_name, columns, schema):
        query = '''BEGIN; {drop}; {create}; {cartodbfy}; COMMIT;'''.format(
            drop=_drop_table_query(table_name),
            create=_create_table_query(table_name, columns),
            cartodbfy=_cartodbfy_query(table_name, schema))
        self.execute_long_running_query(query)

    def _compute_query(self, source, schema):
        if is_sql_query(source):
            print('Debug: SQL query detected')
            return source
        print('Debug: table name detected')
        schema = schema or self.get_schema()
        return compute_query_from_table(source, schema)

    def _check_exists(self, query):
        exists_query = 'SELECT EXISTS ({})'.format(query)
        try:
            self.execute_query(exists_query, do_post=False)
        except Exception as e:
            raise ValueError(e)

    def _get_columns(self, query):
        query = 'SELECT * FROM ({}) _q LIMIT 0'.format(query)
        table_info = self.execute_query(query)
        return Column.from_sql_api_fields(table_info['fields'])

    def _get_copy_query(self, query, columns, limit, keep_the_geom_webmercator):
        query_columns = [
            column.name for column in columns if (column.name != 'the_geom_webmercator'
                                                  or keep_the_geom_webmercator)]

        query = 'SELECT {columns} FROM ({query}) _q'.format(
            query=query,
            columns=','.join(query_columns))

        if limit is not None:
            if isinstance(limit, int) and (limit >= 0):
                query += ' LIMIT {limit}'.format(limit=limit)
            else:
                raise ValueError("`limit` parameter must an integer >= 0")

        return query

    def _copy_to(self, query, columns, retry_times):
        copy_query = 'COPY ({0}) TO stdout WITH (FORMAT csv, HEADER true, NULL \'{1}\')'.format(query, PG_NULL)

        try:
            raw_result = self.copy_client.copyto_stream(copy_query)
        except CartoRateLimitException as err:
            if retry_times > 0:
                retry_times -= 1
                warn('Read call rate limited. Waiting {s} seconds'.format(s=err.retry_after))
                time.sleep(err.retry_after)
                warn('Retrying...')
                return self._copy_to(query, columns, retry_times)
            else:
                warn(('Read call was rate-limited. '
                      'This usually happens when there are multiple queries being read at the same time.'))
                raise err

        index_col = obtain_index_col(columns)
        converters = obtain_converters(columns, decode_geom=True)
        parse_dates = date_columns_names(columns)

        df = pd.read_csv(
            raw_result,
            converters=converters,
            parse_dates=parse_dates)

        if index_col:
            df.index = df[index_col]
            df.index.name = None

        return df

    def _copy_from(self, dataframe, table_name, dataframe_columns_info):
        query = """
            COPY {table_name}({columns}) FROM stdin WITH (FORMAT csv, DELIMITER '|', NULL '{null}');
        """.format(
            table_name=table_name, null=PG_NULL,
            columns=','.join(c.database for c in dataframe_columns_info.columns)).strip()

        data = _rows(dataframe, dataframe_columns_info)

        self.copy_client.copyfrom(query.strip(), data)

    def _normalize_table_name(self, table_name):
        norm_table_name = normalize_name(table_name)
        if norm_table_name != table_name:
            print('Debug: table name normalized: "{}"'.format(norm_table_name))
        return norm_table_name


def _drop_table_query(table_name, if_exists=True):
    return '''DROP TABLE {if_exists} {table_name}'''.format(
        table_name=table_name,
        if_exists='IF EXISTS' if if_exists else '')


def _create_table_query(table_name, columns):
    cols = ['{column} {type}'.format(column=c.database, type=c.database_type) for c in columns]

    return '''CREATE TABLE {table_name} ({cols})'''.format(
        table_name=table_name,
        cols=', '.join(cols))


def _cartodbfy_query(table_name, schema):
    return "SELECT CDB_CartodbfyTable('{schema}', '{table_name}')" \
        .format(schema=schema, table_name=table_name)


def _rows(df, dataframe_columns_info):
    for index, _ in df.iterrows():
        row_data = []
        for c in dataframe_columns_info.columns:
            col = c.dataframe
            if col not in df.columns:
                if df.index.name and col == df.index.name:
                    val = index
                else:  # we could have filtered columns in the df. See DataframeColumnsInfo
                    continue
            else:
                val = df.at[index, col]

            if dataframe_columns_info.geom_column and col == dataframe_columns_info.geom_column:
                geom = decode_geometry(val, dataframe_columns_info.enc_type)
                if geom:
                    val = 'SRID=4326;{}'.format(geom.wkt)
                else:
                    val = ''

            row_data.append(encode_row(val))

        csv_row = b'|'.join(row_data)
        csv_row += b'\n'

        yield csv_row
