"""This module allows users to create interactive vector maps using CARTO VL.
The API for vector maps is broadly similar to :py:meth:`CartoContext.map
<cartoframes.context.CartoContext.map>`, with the exception that all styling
expressions are expected to be straight CARTO VL expressions. See examples in
the `CARTO VL styling guide
<https://carto.com/developers/carto-vl/guides/styling-points/>`__

Here is an example using the example CartoContext from the :py:class:`Examples
<cartoframes.examples.Examples>` class.

.. code::

    from cartoframes.examples import example_context
    from cartoframes.contrib import vector
    vector.vmap(
        [vector.Layer(
            'nat',
            color='ramp(globalEqIntervals($hr90, 7), sunset)',
            stroke_width_=0),
        ],
        example_context)
"""
import os
import json
from warnings import warn
from IPython.display import HTML
import numpy as np
try:
    import geopandas
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

from .. import utils

# CARTO VL
_DEFAULT_CARTO_VL_PATH = 'https://libs.cartocdn.com/carto-vl/v1.1.1/carto-vl.min.js'

# AIRSHIP
_AIRSHIP_SCRIPT = '/packages/components/dist/airship.js'
_AIRSHIP_BRIDGE_SCRIPT = '/packages/bridge/dist/asbridge.js'
_AIRSHIP_STYLE = '/packages/styles/dist/airship.css'
_AIRSHIP_ICONS_STYLE = '/packages/icons/dist/icons.css'

_DEFAULT_AIRSHIP_COMPONENTS_PATH = 'https://libs.cartocdn.com/airship-components/v1.0.3/airship.js'
_DEFAULT_AIRSHIP_BRIDGE_PATH = 'https://libs.cartocdn.com/airship-bridge/v1.0.3/asbridge.js'
_DEFAULT_AIRSHIP_STYLES_PATH = 'https://libs.cartocdn.com/airship-style/v1.0.3/airship.css'
_DEFAULT_AIRSHIP_ICONS_PATH = 'https://libs.cartocdn.com/airship-icons/v1.0.3/icons.css'


class BaseMaps(object):  # pylint: disable=too-few-public-methods
    """Supported CARTO vector basemaps. Read more about the styles in the
    `CARTO Basemaps repository <https://github.com/CartoDB/basemap-styles>`__.

    Attributes:
        darkmatter (str): CARTO's "Dark Matter" style basemap
        positron (str): CARTO's "Positron" style basemap
        voyager (str): CARTO's "Voyager" style basemap

    Example:
        Create an embedded map using CARTO's Positron style with no data layers

        .. code::

            from cartoframes.contrib import vector
            from cartoframes import CartoContext
            cc = CartoContext()
            vector.vmap([], context=cc, basemap=vector.BaseMaps.positron)
    """
    positron = 'Positron'
    darkmatter = 'DarkMatter'
    voyager = 'Voyager'


class QueryLayer(object):  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """CARTO VL layer based on an arbitrary query against user database

    Args:
        query (str): Query against user database. This query must have the
          following columns included to successfully have a map rendered:
          `the_geom`, `the_geom_webmercator`, and `cartodb_id`. If columns are
          used in styling, they must be included in this query as well.
        color_ (str, optional): CARTO VL color styling for this layer. Valid
          inputs are simple web color names and hex values. For more advanced
          styling, see the CARTO VL guide on styling for more information:
          https://carto.com/developers/carto-vl/guides/styling-points/
        width_ (float or str, optional): CARTO VL width styling for this layer if
          points or lines (which are not yet implemented). Valid inputs are
          positive numbers or text expressions involving variables. To remain
          consistent with cartoframes' raster-based :py:class:`Layer
          <cartoframes.layer.Layer>` API, `size` is used here in place of
          `width`, which is the CARTO VL variable name for controlling the
          width of a point or line. Default size is 7 pixels wide.
        filter_ (str, optional): Time expression to animate data. This is an alias
          for the CARTO VL `filter` style attribute. Default is no animation.
        stroke_color_ (str, optional): Defines the stroke color of polygons.
          Default is white.
        stroke_width_ (float or str, optional): Defines the width of the stroke
          in pixels. Default is 1.
        interactivity (str, list, or dict, optional): This option adds
          interactivity (click or hover) to a layer. Defaults to ``click`` if
          one of the following inputs are specified:
          - dict: If a :obj:`dict`, this must have the key `cols` with its
            value a list of columns. Optionally add `event` to choose ``hover``
            or ``click``. Specifying a `header` key/value pair adds a header to
            the popup that will be rendered in HTML.
          - list: A list of valid column names in the data used for this layer
          - str: A column name in the data used in this layer

    Example:

        .. code::

            from cartoframes.examples import example_context
            from cartoframes.contrib import vector
            # create geometries from lng/lat columns
            q = '''
               SELECT *, ST_Transform(the_geom, 3857) as the_geom_webmercator
               FROM (
                   SELECT
                     CDB_LatLng(pickup_latitude, pickup_longitude) as the_geom,
                     fare_amount,
                     cartodb_id
                   FROM taxi_50k
               ) as _w
            '''
            vector.vmap(
                [vector.QueryLayer(q), ],
                example_context,
                interactivity={
                    'cols': ['fare_amount', ],
                    'event': 'hover'
                }
            )
    """

    def __init__(self,
                 query,
                 color_=None,
                 width_=None,
                 filter_=None,
                 stroke_color_=None,
                 stroke_width_=None,
                 transform_=None,
                 order_=None,
                 symbol_=None,
                 variables=None,
                 interactivity=None,
                 legend=None):

        def convstr(obj):
            """convert all types to strings or None"""
            return str(obj) if obj is not None else None

        # data source
        self.query = query

        # style attributes
        self.color_ = color_
        self.width_ = convstr(width_)
        self.filter_ = filter_
        self.stroke_color_ = stroke_color_  # pylint: disable=invalid-name
        self.stroke_width_ = convstr(stroke_width_)  # pylint: disable=invalid-name
        self.transform_ = transform_
        self.order_ = order_
        self.symbol_ = symbol_

        # legends
        self.legend = legend

        # internal attributes
        self.orig_query = query
        self.is_basemap = False
        self.styling = ''
        self.interactivity = None
        self.header = None

        self._compose_style()

        # variables
        self._set_variables(variables)

        # interactivity options
        self._set_interactivity(interactivity)

    def _compose_style(self):
        """Appends `prop` with `style` to layer styling"""
        valid_styles = (
            'color',
            'width',
            'filter',
            'stroke_width',
            'stroke_color',
            'transform',
            'order',
            'symbol'
        )
        self.styling = '\n'.join(
            '{prop}: {style}'.format(prop=to_camel_case(s),
                                     style=getattr(self, s + '_'))
            for s in valid_styles
            if getattr(self, s + '_') is not None
        )

    def _set_variables(self, variables):
        # TODO add check
        if variables is None:
            self.variables = None
            return
        elif isinstance(variables, (list)):
            self.variables = variables
            variables_list = '\n'.join(
                '{name}: {value}'.format(
                    name=variable[0],
                    value=variable[1]
                ) for variable in variables
            )
        else:
            raise ValueError('`variables` must be a list of [ name, value ]')

        self.styling = '\n'.join([variables_list, self.styling])

    def _set_interactivity(self, interactivity):
        """Adds interactivity syntax to the styling"""
        event_default = 'hover'
        if interactivity is None:
            return
        elif isinstance(interactivity, dict):
            self.interactivity = interactivity.get('event', event_default)
            self.header = interactivity.get('header')
        else:
            raise ValueError('`interactivity` must be a dictionary')


def _get_html_doc(sources,
                  bounds,
                  creds=None,
                  basemap=None,
                  _carto_vl_path=_DEFAULT_CARTO_VL_PATH,
                  _airship_path=None):
    html_template = os.path.join(
        os.path.dirname(__file__),
        '..',
        'assets',
        'vector.html'
    )
    token = ''

    with open(html_template, 'r') as html_file:
        srcdoc = html_file.read()

    credentials = {
        'username': creds.username(),
        'api_key': creds.key(),
        'base_url': creds.base_url()
    }
    if isinstance(basemap, dict):
        token = basemap.get('token', '')
        if 'style' not in basemap:
            raise ValueError(
                'If basemap is a dict, it must have a `style` key'
            )
        if not token and basemap.get('style').startswith('mapbox://'):
            warn('A Mapbox style usually needs a token')
        basemap = basemap.get('style')

    if (_airship_path is None):
        airship_components_path = _DEFAULT_AIRSHIP_COMPONENTS_PATH
        airship_bridge_path = _DEFAULT_AIRSHIP_BRIDGE_PATH
        airship_styles_path = _DEFAULT_AIRSHIP_STYLES_PATH
        airship_icons_path = _DEFAULT_AIRSHIP_ICONS_PATH
    else:
        airship_components_path = _airship_path + _AIRSHIP_SCRIPT
        airship_bridge_path = _airship_path + _AIRSHIP_BRIDGE_SCRIPT
        airship_styles_path = _airship_path + _AIRSHIP_STYLE
        airship_icons_path = _airship_path + _AIRSHIP_ICONS_STYLE

    return srcdoc.replace('@@SOURCES@@', json.dumps(sources)) \
        .replace('@@BASEMAPSTYLE@@', basemap) \
        .replace('@@MAPBOXTOKEN@@', token) \
        .replace('@@CREDENTIALS@@', json.dumps(credentials)) \
        .replace('@@BOUNDS@@', bounds) \
        .replace('@@CARTO_VL_PATH@@', _carto_vl_path) \
        .replace('@@AIRSHIP_COMPONENTS_PATH@@', airship_components_path) \
        .replace('@@AIRSHIP_BRIDGE_PATH@@', airship_bridge_path) \
        .replace('@@AIRSHIP_STYLES_PATH@@', airship_styles_path) \
        .replace('@@AIRSHIP_ICONS_PATH@@', airship_icons_path)


class Layer(QueryLayer):  # pylint: disable=too-few-public-methods
    """Layer from a table name. See :py:class:`vector.QueryLayer
    <cartoframes.contrib.vector.QueryLayer>` for docs on the style attributes.

    Example:

        Visualize data from a table. Here we're using the example CartoContext.
        To use this with your account, replace the `example_context` with your
        :py:class:`CartoContext <cartoframes.context.CartoContext>` and a table
        in the account you authenticate against.

        .. code::

            from cartoframes.examples import example_context
            from cartoframes.contrib import vector
            vector.vmap(
                [vector.Layer(
                    'nat',
                    color='ramp(globalEqIntervals($hr90, 7), sunset)',
                    stroke_width_=0),
                ],
                example_context)
    """
    def __init__(self,
                 table_name,
                 color_=None,
                 width_=None,
                 filter_=None,
                 stroke_color_=None,
                 stroke_width_=None,
                 transform_=None,
                 order_=None,
                 symbol_=None,
                 variables=None,
                 legend=None,
                 interactivity=None):

        self.table_source = table_name

        super(Layer, self).__init__(
            'SELECT * FROM {}'.format(table_name),
            color_=color_,
            width_=width_,
            filter_=filter_,
            stroke_color_=stroke_color_,
            stroke_width_=stroke_width_,
            transform_=transform_,
            order_=order_,
            symbol_=symbol_,
            variables=variables,
            legend=legend,
            interactivity=interactivity
        )


class LocalLayer(QueryLayer):  # pylint: disable=too-few-public-methods
    """Create a layer from a GeoDataFrame

    TODO: add support for filepath to a GeoJSON file, JSON/dict, or string

    See :obj:`QueryLayer` for the full styling documentation.

    Example:
        In this example, we grab data from the cartoframes example account
        using `read_mcdonals_nyc` to get McDonald's locations within New York
        City. Using the `decode_geom=True` argument, we decode the geometries
        into a form that works with GeoPandas. Finally, we pass the
        GeoDataFrame into :py:class:`LocalLayer
        <cartoframes.contrib.vector.LocalLayer>` to visualize.

        .. code::

            import geopandas as gpd
            from cartoframes.examples import read_mcdonalds_nyc, example_context
            from cartoframes.contrib import vector
            gdf = gpd.GeoDataFrame(read_mcdonalds_nyc(decode_geom=True))
            vector.vmap([vector.LocalLayer(gdf), ], context=example_context)
    """
    def __init__(self,
                 dataframe,
                 color_=None,
                 width_=None,
                 filter_=None,
                 stroke_color_=None,
                 stroke_width_=None,
                 transform_=None,
                 order_=None,
                 symbol_=None,
                 variables=None,
                 legend=None,
                 interactivity=None):
        if HAS_GEOPANDAS and isinstance(dataframe, geopandas.GeoDataFrame):
            # filter out null geometries
            _df_nonnull = dataframe[~dataframe.geometry.isna()]
            # convert time cols to epoch
            timecols = _df_nonnull.select_dtypes(
                    include=['datetimetz', 'datetime', 'timedelta']).columns
            for timecol in timecols:
                _df_nonnull[timecol] = _df_nonnull[timecol].astype(np.int64)
            self._geojson_str = _df_nonnull.to_json()
            self.bounds = _df_nonnull.total_bounds.tolist()
        else:
            raise ValueError('LocalLayer only works with GeoDataFrames from '
                             'the geopandas package')

        super(LocalLayer, self).__init__(
            query=None,
            color_=color_,
            width_=width_,
            filter_=filter_,
            stroke_color_=stroke_color_,
            stroke_width_=stroke_width_,
            transform_=transform_,
            order_=order_,
            symbol_=symbol_,
            variables=variables,
            legend=legend,
            interactivity=interactivity
        )

@utils.temp_ignore_warnings
def vmap(layers,
         context,
         size=(1024, 632),
         basemap=BaseMaps.voyager,
         bounds=None,
         **kwargs):

    """CARTO VL-powered interactive map

    Args:
        layers (list of Layer-types): List of layers. One or more of
          :py:class:`Layer <cartoframes.contrib.vector.Layer>`,
          :py:class:`QueryLayer <cartoframes.contrib.vector.QueryLayer>`, or
          :py:class:`LocalLayer <cartoframes.contrib.vector.LocalLayer>`.
        context (:py:class:`CartoContext <cartoframes.context.CartoContext>`):
          A :py:class:`CartoContext <cartoframes.context.CartoContext>`
          instance
        size (tuple of int): a (width, height) pair for the size of the map.
          Default is (1024, 632)
        basemap (str):
          - if a `str`, name of a CARTO vector basemap. One of `positron`,
            `voyager`, or `darkmatter` from the :obj:`BaseMaps` class
          - if a `dict`, Mapbox or other style as the value of the `style` key.
            If a Mapbox style, the access token is the value of the `token`
            key.
        bounds (dict or list): a dict with `east`,`north`,`west`,`south`
          properties, or a list of floats in the following order: [west,
          south, east, north]. If not provided the bounds will be automatically
          calculated to fit all features.

    Example:

        .. code::

            from cartoframes.contrib import vector
            from cartoframes import CartoContext
            cc = CartoContext(
                base_url='https://your_user_name.carto.com',
                api_key='your api key'
            )
            vector.vmap([vector.Layer('table in your account'), ], cc)

        CARTO basemap style.

        .. code::

            from cartoframes.contrib import vector
            from cartoframes import CartoContext
            cc = CartoContext(
                base_url='https://your_user_name.carto.com',
                api_key='your api key'
            )
            vector.vmap(
                [vector.Layer('table in your account'), ],
                context=cc,
                basemap=vector.BaseMaps.darkmatter
            )

        Custom basemap style. Here we use the Mapbox streets style, which
        requires an access token.

        .. code::

            from cartoframes.contrib import vector
            from cartoframes import CartoContext
            cc = CartoContext(
                base_url='https://<username>.carto.com',
                api_key='your api key'
            )
            vector.vmap(
                [vector.Layer('table in your account'), ],
                context=cc,
                basemap={
                    'style': 'mapbox://styles/mapbox/streets-v9',
                    'token: '<your mapbox token>'
                }
            )

        Custom bounds

        .. code::

            from cartoframes.contrib import vector
            from cartoframes import CartoContext
            cc = CartoContext(
                base_url='https://<username>.carto.com',
                api_key='your api key'
            )
            vector.vmap(
                [vector.Layer('table in your account'), ],
                context=cc,
                bounds={'west': -10, 'east': 10, 'north': -10, 'south': 10}
            )
    """
    if bounds:
        bounds = _format_bounds(bounds)
    else:
        bounds = _get_super_bounds(layers, context)

    jslayers = []
    for _, layer in enumerate(layers):
        is_local = isinstance(layer, LocalLayer)
        intera = (
            dict(event=layer.interactivity, header=layer.header)
            if layer.interactivity is not None
            else None
        )
        jslayers.append({
            'is_local': is_local,
            'styling': layer.styling,
            'source': layer._geojson_str if is_local else layer.query,
            'interactivity': intera,
            'legend': layer.legend
        })

    _carto_vl_path = kwargs.get('_carto_vl_path', _DEFAULT_CARTO_VL_PATH)
    _airship_path = kwargs.get('_airship_path', None)

    html = (
        '<iframe srcdoc="{content}" width="{width}" height="{height}">'
        '</iframe>'
        ).format(
            width=size[0],
            height=size[1],
            content=utils.safe_quotes(
                _get_html_doc(
                    jslayers,
                    bounds,
                    context.creds,
                    basemap=basemap,
                    _carto_vl_path=_carto_vl_path,
                    _airship_path=_airship_path)
            )
        )
    return HTML(html)


def _format_bounds(bounds):
    if isinstance(bounds, dict):
        return _dict_bounds(bounds)

    return _list_bounds(bounds)


def _list_bounds(bounds):
    if len(bounds) != 4:
        raise ValueError('bounds list must have exactly four values in the '
                         'order: [west, south, east, north]')

    return _dict_bounds({
        'west': bounds[0],
        'south': bounds[1],
        'east': bounds[2],
        'north': bounds[3]
    })


def _dict_bounds(bounds):
    if 'west' not in bounds or 'east' not in bounds or 'north' not in bounds\
            or 'south' not in bounds:
        raise ValueError('bounds must have east, west, north and '
                         'south properties')

    return '[[{west}, {south}], [{east}, {north}]]'.format(**bounds)


def _get_super_bounds(layers, context):
    """"""
    hosted_layers = [
        layer for layer in layers
        if not isinstance(layer, LocalLayer)
    ]
    local_layers = [
        layer for layer in layers
        if isinstance(layer, LocalLayer)
    ]
    hosted_bounds = dict.fromkeys(['west', 'south', 'east', 'north'])
    local_bounds = dict.fromkeys(['west', 'south', 'east', 'north'])

    if hosted_layers:
        hosted_bounds = context._get_bounds(hosted_layers)  # pylint: disable=protected-access
    if local_layers:
        local_bounds = _get_bounds_local(local_layers)

    bounds = _combine_bounds(hosted_bounds, local_bounds)

    return _format_bounds(bounds)


def _get_bounds_local(layers):
    """Aggregates bounding boxes of all local layers

        return: dict of bounding box of all bounds in layers
    """
    if not layers:
        return {'west': None, 'south': None, 'east': None, 'north': None}

    bounds = layers[0].bounds

    for layer in layers[1:]:
        bounds = np.concatenate(
            (
                np.minimum(
                    bounds[:2],
                    layer.bounds[:2]
                ),
                np.maximum(
                    bounds[2:],
                    layer.bounds[2:]
                )
            )
        )

    return dict(zip(['west', 'south', 'east', 'north'], bounds))


def _combine_bounds(bbox1, bbox2):
    """Takes two bounding boxes dicts and gives a new bbox that encompasses
    them both"""
    WORLD = {'west': -180, 'south': -85.1, 'east': 180, 'north': 85.1}
    ALL_KEYS = set(WORLD.keys())

    def dict_all_nones(bbox_dict):
        """Returns True if all dict values are None"""
        return all(v is None for v in bbox_dict.values())

    # if neither are defined, use the world
    if not bbox1 and not bbox2:
        return WORLD
    # if all nones, use the world
    if dict_all_nones(bbox1) and dict_all_nones(bbox2):
        return WORLD

    assert ALL_KEYS == set(bbox1.keys()) and ALL_KEYS == set(bbox2.keys()),\
        'Input bounding boxes must have the same dictionary keys'
    # create dict with cardinal directions and None-valued keys
    outbbox = dict.fromkeys(['west', 'south', 'east', 'north'])

    def conv2nan(val):
        """convert Nones to np.nans"""
        return np.nan if val is None else val

    # set values and/or defaults
    for coord in ('north', 'east'):
        outbbox[coord] = np.nanmax([
                conv2nan(bbox1[coord]),
                conv2nan(bbox2[coord])
            ])
    for coord in ('south', 'west'):
        outbbox[coord] = np.nanmin([
                conv2nan(bbox1[coord]),
                conv2nan(bbox2[coord])
            ])

    return outbbox


def to_camel_case(snake_str):
    components = snake_str.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])
