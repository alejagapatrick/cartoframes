import os

from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from ...utils.logger import log
from ...utils.utils import dtypes2vl

GEOID_KEY = 'geoid'
GEOM_KEY = 'geom'
PROJECT_KEY = 'GOOGLE_CLOUD_PROJECT'


class GBQManager:

    DATA_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB

    def __init__(self, project=None, token=None):
        credentials = Credentials(token) if token else None

        self.token = token
        self.project = project if project else os.environ[PROJECT_KEY]
        self.client = bigquery.Client(project=project, credentials=credentials)

    def download_dataframe(self, query):
        query_job = self.client.query(query)
        return query_job.to_dataframe()

    def fetch_mvt_data(self, query):
        # TODO: implement MVT data request
        fake_dataset = 'jarroyo'
        fake_table = 'geography_usa_block_2019_12_mvts'
        return {
            'projectId': self.project,
            'datasetId': fake_dataset,
            'tableId': fake_table,
            'token': self.token
        }

    def fetch_mvt_metadata(self, query):
        metadata_query = '''
            WITH q as ({})
            SELECT * FROM q LIMIT 1
        '''.format(query)

        result = self.client.query(metadata_query).to_dataframe()

        if GEOID_KEY not in result.columns:
            raise ValueError('No "geoid" column found.')

        properties = {}
        for column in result.columns:
            if column == GEOM_KEY:
                continue
            dtype = result.dtypes[column]
            properties[column] = {'type': dtypes2vl(dtype)}

        return {
            'idProperty': GEOID_KEY,
            'properties': properties
        }

    def compute_zoom_function(self, query):
        # TODO: implement
        return '''
            (zoom) => {
                if (zoom >= 12) {
                    return 12;
                }
                return null;
            }
        '''

    def trigger_mvt_generation(self, query):
        # TODO: update MVT generation query
        # generation_query = '''
        #     WITH q as ({})
        #     CREATE TABLE ...
        # '''
        # self.client.query(generation_query)
        pass

    def estimated_data_size(self, query):
        log.info('Estimating size. This may take a few secods')
        estimation_query = '''
            WITH q as ({})
            SELECT SUM(CHAR_LENGTH(ST_ASTEXT(geom))) AS s FROM q
        '''.format(query)
        estimation_query_job = self.client.query(estimation_query)
        result = estimation_query_job.to_dataframe()
        estimated_size = result.s[0] * 0.425
        if estimated_size < self.DATA_SIZE_LIMIT:
            log.info('DEBUG: small dataset ({:.2f} KB)'.format(estimated_size / 1024))
        else:
            log.info('DEBUG: big dataset ({:.2f} MB)'.format(estimated_size / 1024 / 1024))
        return estimated_size

    def get_total_bytes_processed(self, query):
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        query_job = self.client.query(query, job_config=job_config)
        return query_job.total_bytes_processed