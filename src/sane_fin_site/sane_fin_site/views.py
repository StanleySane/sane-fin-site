import posixpath

from django.views.static import serve


def static_handler(request, path, **kwargs):
    """ This view is a wrapper over ``django.views.static.serve``

    Call ``django.views.static.serve``
    and replace 'Content-Type' header of response to 'application/javascript' for .js files
    because default implementation considers them as 'text/plain'
    so they are not executable in the resulting HTML.
    """
    response = serve(request, path, **kwargs)

    _, ext = posixpath.splitext(path)
    if ext.lower() == '.js':
        response.headers['Content-Type'] = 'application/javascript'

    return response
