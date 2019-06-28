import re
import time
import binascii as ba
from warnings import warn
from copy import deepcopy

from carto.exceptions import CartoException, CartoRateLimitException

from ..columns import Column

try:
    import geopandas
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False


DEFAULT_RETRY_TIMES = 3

GEOM_COLUMN_NAMES = [
    'geometry',
    'the_geom',
    'wkt_geometry',
    'wkb_geometry',
    'geom',
    'wkt',
    'wkb'
]

LAT_COLUMN_NAMES = [
    'latitude',
    'lat'
]

LNG_COLUMN_NAMES = [
    'longitude',
    'lng',
    'lon',
    'long'
]


def compute_query(dataset):
    if dataset.table_name:
        return 'SELECT * FROM "{schema}"."{table}"'.format(
            schema=dataset.schema or dataset._get_schema() or 'public',
            table=dataset.table_name
        )


def compute_geodataframe(dataset):
    if HAS_GEOPANDAS and dataset.dataframe is not None:
        df = dataset.dataframe
        geom_column = _get_column(df, GEOM_COLUMN_NAMES)
        if geom_column is not None:
            df['geometry'] = _compute_geometry_from_geom(geom_column)
            _warn_new_geometry_column(df)
        else:
            lat_column = _get_column(df, LAT_COLUMN_NAMES)
            lng_column = _get_column(df, LNG_COLUMN_NAMES)
            if lat_column is not None and lng_column is not None:
                df['geometry'] = _compute_geometry_from_latlng(lat_column, lng_column)
                _warn_new_geometry_column(df)
            else:
                raise ValueError('''No geographic data found. '''
                                 '''If a geometry exists, change the column name ({0}) or '''
                                 '''ensure it is a DataFrame with a valid geometry. '''
                                 '''If there are latitude/longitude columns, rename to ({1}), ({2}).'''.format(
                                     ', '.join(GEOM_COLUMN_NAMES),
                                     ', '.join(LAT_COLUMN_NAMES),
                                     ', '.join(LNG_COLUMN_NAMES)
                                 ))
        return geopandas.GeoDataFrame(df)


def _get_column(df, options):
    for name in options:
        if name in df:
            return df[name]


def _warn_new_geometry_column(df):
    if 'geometry' not in df:
        warn('A new "geometry" column has been added to the original dataframe.')


def _compute_geometry_from_geom(geom):
    first_el = geom[0]
    enc_type = detect_encoding_type(first_el)
    return geom.apply(lambda g: decode_geometry(g, enc_type))


def _compute_geometry_from_latlng(lat, lng):
    from shapely import geometry
    return [geometry.Point(xy) for xy in zip(lng, lat)]


def _encode_decode_decorator(func):
    """decorator for encoding and decoding geoms"""
    def wrapper(*args):
        """error catching"""
        try:
            processed_geom = func(*args)
            return processed_geom
        except ImportError as err:
            raise ImportError('The Python package `shapely` needs to be '
                              'installed to encode or decode geometries. '
                              '({})'.format(err))
    return wrapper


@_encode_decode_decorator
def decode_geometry(geom, enc_type):
    """Decode any geometry into a shapely geometry"""
    from shapely import wkb
    from shapely import wkt

    func = {
        'shapely': lambda: geom,
        'wkb': lambda: wkb.loads(geom),
        'wkb-hex': lambda: wkb.loads(ba.unhexlify(geom)),
        'wkb-hex-ascii': lambda: wkb.loads(geom, hex=True),
        'ewkb-hex-ascii': lambda: wkb.loads(_remove_srid(geom), hex=True),
        'wkt': lambda: wkt.loads(geom),
        'ewkt': lambda: wkt.loads(_remove_srid(geom))
    }.get(enc_type)

    if func:
        return func()
    else:
        raise ValueError('Encoding type "{}" not supported'.format(enc_type))


def detect_encoding_type(input_geom):
    """
    Detect geometry encoding type:
    - 'wkb': b'\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00H\x93@\x00\x00\x00\x00\x00\x9d\xb6@'
    - 'wkb-hex': b'0101000000000000000048934000000000009db640'
    - 'wkb-hex-ascii': '0101000000000000000048934000000000009db640'
    - 'ewkb-hex-ascii': 'SRID=4326;0101000000000000000048934000000000009db640'
    - 'wkt': 'POINT (1234 5789)'
    - 'ewkt': 'SRID=4326;POINT (1234 5789)'
    """
    from shapely.geometry.base import BaseGeometry

    if isinstance(input_geom, BaseGeometry):
        return 'shapely'

    if isinstance(input_geom, bytes):
        try:
            ba.unhexlify(input_geom)
            return 'wkb-hex'
        except Exception:
            return 'wkb'

    if isinstance(input_geom, str):
        result = re.match(r'^SRID=\d+;(.*)$', input_geom)
        prefix = 'e' if result else ''
        geom = result.group(1) if result else input_geom
    
        if re.match(r'^[0-9a-fA-F]+$', geom):
            return prefix + 'wkb-hex-ascii'
        else:
            return prefix + 'wkt'
    
    raise ValueError('Wrong input geometry.')


def _remove_srid(text):
    result = re.match(r'^SRID=\d+;(.*)$', text)
    return result.group(1) if result else text


def recursive_read(context, query, retry_times=DEFAULT_RETRY_TIMES):
    try:
        return context.copy_client.copyto_stream(query)
    except CartoRateLimitException as err:
        if retry_times > 0:
            retry_times -= 1
            warn('Read call rate limited. Waiting {s} seconds'.format(s=err.retry_after))
            time.sleep(err.retry_after)
            warn('Retrying...')
            return recursive_read(context, query, retry_times=retry_times)
        else:
            warn(('Read call was rate-limited. '
                  'This usually happens when there are multiple queries being read at the same time.'))
            raise err


def get_columns(context, query):
    col_query = '''SELECT * FROM ({query}) _q LIMIT 0'''.format(query=query)
    table_info = context.sql_client.send(col_query)
    return Column.from_sql_api_fields(table_info['fields'])


def setting_value_exception(prop, value):
    return CartoException(("Error setting {prop}. You must use the `update` method: "
                           "dataset_info.update({prop}='{value}')").format(prop=prop, value=value))


def get_public_context(context):
    api_key = 'default_public'

    public_context = deepcopy(context)
    public_context.auth_client.api_key = api_key
    public_context.auth_api_client.api_key = api_key

    return public_context
