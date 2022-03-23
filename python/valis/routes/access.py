# !/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Filename: access.py
# Project: routes
# Author: Brian Cherinka
# Created: Wednesday, 16th September 2020 6:16:40 pm
# License: BSD 3-clause "New" or "Revised" License
# Copyright (c) 2020 Brian Cherinka
# Last Modified: Wednesday, 16th September 2020 6:16:40 pm
# Modified By: Brian Cherinka


from __future__ import print_function, division, absolute_import
from sdss_access.path import Path
from fastapi import APIRouter, Request, Depends, HTTPException, Body
from fastapi_utils.cbv import cbv
from pydantic import BaseModel, root_validator, validator, PrivateAttr, Field
from typing import Type, Optional
from enum import Enum
from pydantic import ValidationError

from valis.routes.base import Base, get_access, BaseBody

class PathPart(str, Enum):
    """ A set of pre-defined choices for the `part` query param """
    full = "full"
    url = "url"
    file = "file"
    location = "location"
    all = "all"


class PathModel(BaseModel):
    """ A validator class for sdss_access path names and kwargs """
    name: str
    kwargs: dict = {}
    template: str = None
    full: str = None
    url: str = None
    file: str = None
    location: str = None
    exists: bool = None
    needs_kwargs: bool = None
    _path: Path = PrivateAttr()  # private attr so model has correct sdss_access pat

    def __new__(cls, *args, **kwargs):
        cls._path = kwargs.get('_path', None)
        return super(PathModel, cls).__new__(cls)

    @validator('name')
    def is_name(cls, v, values):
        if v not in cls._path.lookup_names():
            release = 'WORK' if cls._path.release in ('sdss5', 'sdss4', 'sdsswork') else cls._path.release.upper()
            raise ValueError(f'Validation error: path name {v} not a valid sdss_access name for release {release}')
        return v

    @validator('kwargs')
    def good_kwargs(cls, v, values):
        name = values.get('name')
        keys = set(cls._path.lookup_keys(name))

        # check for valid
        valid = set(v) & set(keys)
        if not valid:
            return {}

        # check for missing kwargs
        missing = set(keys) - set(v)
        if missing:
            mstr = ', '.join(missing)
            raise ValueError(f'Validation error: Missing kwargs {mstr} for name: {name}')
        return v
    
    @validator('needs_kwargs', always=True)
    def check_kwargs(cls, v, values):
        ''' Check and assign the needs_kwargs attribute'''
        return any(cls._path.lookup_keys(values.get('name')))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.template = self._path.templates[self.name]
        if self.kwargs or not self.needs_kwargs:
            self.full = self._path.full(self.name, **self.kwargs)
            self.url = self._path.url(self.name, **self.kwargs)
            self.file = self._path.name(self.name, **self.kwargs)
            self.location = self._path.location(self.name, **self.kwargs)
            self.exists = self._path.exists(self.name, **self.kwargs)

class PathBody(BaseBody):
    kwargs: dict = {}
    part: PathPart = 'full'
    exists: bool = False


async def extract_path(name: str, request: Request, access: Path = Depends(get_access)) -> Type[PathModel]:
    """ Dependency to extract and parse generic query parameters """
    params = str(request.query_params)
    kwargs = dict(map(lambda x: x.split('='), params.split('&'))) if params else {}
    try:
        path = PathModel(name=name, kwargs=kwargs, _path=access)
    except ValidationError as ee:
        raise HTTPException(status_code=422, detail=ee.errors()) from ee
    else:
        return path

router = APIRouter()

@cbv(router)
class Paths(Base):

    @router.get("/", summary='Get a list of all sdss_access path names or templates')
    async def get_paths(self, templates: bool = False):
        """ Get a list of sdss_access path names """
        if templates:
            return self.path.templates
        else:
            return {'names': list(self.path.lookup_names())}

    @router.get("/keywords/{name}", summary='Get a list of keyword variables for a sdss_acccess path name.')
    async def get_path_kwargs(self, path: Type[PathModel] = Depends(extract_path)):
        """ Get a list of input keyword arguments

        Given an sdss_access path name, get the list of input keywords needed
        to construct the full path.

        Parameters
        ----------
            name : str
                a sdss_access path name

        Returns
        -------
            A dict of path name and list of string keywords
        """
        return {'name': path.name, 'kwargs': self.path.lookup_keys(path.name)}


    @router.get("/{name}", summary='Get the template or resolved path for an sdss_access path name.')
    async def get_path_name(self, path: Type[PathModel] = Depends(extract_path), part: PathPart = 'full',
                            exists: bool = False):
        """ Construct an sdss_access path

        Given a sdss_access path name, constructs the fully resolved path.  sdss_access path
        keyword arguments are passed in as url query parameters,
        e.g. `paths/mangacube?drpver=v2_4_3&wave=LOG&plate=8485&ifu=1901`.  When no query
        parameters, are specified, returns the sdss_access template.

        Parameters
        ----------
            name : str
                a sdss_access path name
            part : str
                the part of the path to extract
            exists : bool
                If set, checks for local file existence and returns True/False

        Returns
        -------
            A string path name

        """
        return self.process_path(path, part, exists)

    @router.post("/{name}", summary='Get the template or resolved path for an sdss_access path name.')
    async def post_path_name(self, name: str, body: PathBody = None):
        """ Construct an sdss_access path

        Given an sdss_access path name and set of input keyword arguments,
        construct the file path using sdss_access methods.  Set `part` keyword to
        indicate the part of the path to form, e.g. "full", "url".  Set `exists` to
        check whether the file exists on the server.

        Parameters
        ----------
            name : str
                a sdss_access path name
            kwargs: dict
                a set of keyword arguments to construct the file path
            part : str
                the part of the path to extract.  Default is "full".
            exists : bool
                If set, checks for local file existence and returns True/False

        Returns
        -------
            A string path name

        """
        print('name', name)
        print('post', body)
        # if no kwargs set to empty dict
        kwargs = body.kwargs or {}
        try:
            path = PathModel(name=name, kwargs=kwargs, _path=self.path)
        except ValidationError as ee:
            raise HTTPException(status_code=422, detail=ee.errors()) from ee
        else:
            return self.process_path(path, body.part, body.exists)

    def process_path(self, path: Type[PathModel], part: PathPart, exists: bool) -> dict:
        if not path.kwargs and path.needs_kwargs:
            out = path.dict(include={'template'})
            out['warning'] = 'Warning: No kwargs specified to construct a path.  Returning only template.'
            return out
        elif exists:
            return path.dict(include={'exists'})
        else:
            return path.dict() if part == 'all' else path.dict(include={part})
