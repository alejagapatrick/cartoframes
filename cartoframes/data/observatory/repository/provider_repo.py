from cartoframes.data.observatory.repository.repo_client import RepoClient


def get_provider_repo():
    return _REPO


class ProviderRepository(object):

    def __init__(self):
        self.client = RepoClient()

    def all(self):
        return self._to_providers(self.client.get_providers())

    def by_id(self, provider_id):
        result = self.client.get_providers('id', provider_id)

        if len(result) == 0:
            return None

        return self._to_provider(result[0])

    @staticmethod
    def _to_provider(result):
        from cartoframes.data.observatory.provider import Provider

        return Provider({
            'id': result['id'],
            'name': result['name']
        })

    @staticmethod
    def _to_providers(results):
        from cartoframes.data.observatory.provider import Providers

        return Providers([ProviderRepository._to_provider(result) for result in results])


_REPO = ProviderRepository()
