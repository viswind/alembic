from .impl import DefaultImpl

class CrateImpl(DefaultImpl):
    __dialect__ = 'crate'
    transactional_ddl = False
