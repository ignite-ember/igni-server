"""The model providers a backend can be: the customer's own nodes, or a cloud's.

A backend's `kind` says who serves it and how a request to it is
addressed and signed; its *wire* says which API it speaks. Each kind
speaks exactly one wire, and igni server does not translate between
them: a model is served on the endpoint of its own API.

Deliberately empty of re-exports, for the reason `app/services/ai`'s own
package docstring gives. Import from the module that defines the thing.
"""
