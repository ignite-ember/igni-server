from starlette.middleware.base import BaseHTTPMiddleware

#: Public by nature and fetched by every service that checks a token, so
#: they say how long they may be kept rather than being forced uncached.
#: See app/api/base_v1/endpoints/identity/discovery.py.
CACHEABLE_PREFIX = '/.well-known/'

#: Personal answers that say themselves how long the person's own client
#: may keep them (`Cache-Control: private, …`). An error from the same path
#: sets nothing, and is made uncacheable like every other response.
#: See app/api/base_v1/endpoints/cloud/my_cloud.py.
PRIVATELY_CACHEABLE = frozenset({'/v1/me/cloud'})


class DisableCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(CACHEABLE_PREFIX):
            return response
        if request.url.path in PRIVATELY_CACHEABLE and response.headers.get('Cache-Control', '').startswith('private'):
            return response
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
