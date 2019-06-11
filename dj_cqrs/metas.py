from __future__ import unicode_literals

from itertools import chain

from django.db.models import base

from dj_cqrs.constants import ALL_BASIC_FIELDS
from dj_cqrs.registries import MasterRegistry, ReplicaRegistry
from dj_cqrs.signals import MasterSignals


class MasterMeta(base.ModelBase):
    def __new__(mcs, *args):
        model_cls = super(MasterMeta, mcs).__new__(mcs, *args)

        if args[0] != 'MasterMixin':
            _MetaUtils.check_cqrs_id(model_cls)
            MasterMeta._check_correct_configuration(model_cls)
            if model_cls.CQRS_SERIALIZER is None:
                MasterMeta._check_cqrs_fields(model_cls)

            MasterRegistry.register_model(model_cls)
            MasterSignals.register_model(model_cls)

        return model_cls

    @staticmethod
    def _check_correct_configuration(model_cls):
        """ Check that model has correct CQRS configuration.

        :param dj_cqrs.mixins.MasterMixin model_cls: CQRS Master Model.
        :raises: AssertionError
        """
        if model_cls.CQRS_FIELDS != ALL_BASIC_FIELDS:
            assert model_cls.CQRS_SERIALIZER is None, \
                "Model {}: CQRS_FIELDS can't be set together with CQRS_SERIALIZER.".format(
                    model_cls,
                )

    @staticmethod
    def _check_cqrs_fields(model_cls):
        """ Check that model has correct CQRS fields configuration.

        :param dj_cqrs.mixins.MasterMixin model_cls: CQRS Master Model.
        :raises: AssertionError
        """
        if model_cls.CQRS_FIELDS != ALL_BASIC_FIELDS:
            cqrs_field_names = list(model_cls.CQRS_FIELDS)
            _MetaUtils.check_cqrs_field_setting(model_cls, cqrs_field_names, 'CQRS_FIELDS')


class ReplicaMeta(base.ModelBase):
    def __new__(mcs, *args):
        model_cls = super(ReplicaMeta, mcs).__new__(mcs, *args)

        if args[0] != 'ReplicaMixin':
            _MetaUtils.check_cqrs_id(model_cls)
            ReplicaMeta._check_cqrs_mapping(model_cls)

            ReplicaRegistry.register_model(model_cls)

        return model_cls

    @staticmethod
    def _check_cqrs_mapping(model_cls):
        """ Check that model has correct CQRS mapping configuration.

        :param dj_cqrs.mixins.ReplicaMixin model_cls: CQRS Replica Model.
        :raises: AssertionError
        """
        if model_cls.CQRS_MAPPING is not None:
            cqrs_field_names = list(model_cls.CQRS_MAPPING.values())
            _MetaUtils.check_cqrs_field_setting(model_cls, cqrs_field_names, 'CQRS_MAPPING')


class _MetaUtils(object):
    @classmethod
    def check_cqrs_field_setting(cls, model_cls, cqrs_field_names, cqrs_attr):
        cls._check_no_duplicate_names(model_cls, cqrs_field_names, cqrs_attr)
        cls._check_id_in_names(model_cls, cqrs_field_names, cqrs_attr)
        cls._check_unexisting_names(model_cls, cqrs_field_names, cqrs_attr)

    @staticmethod
    def check_cqrs_id(model_cls):
        """ Check that CQRS Model has CQRS_ID set up. """
        assert model_cls.CQRS_ID, 'CQRS_ID must be set for every model, that uses CQRS.'

    @staticmethod
    def _check_no_duplicate_names(model_cls, cqrs_field_names, cqrs_attr):
        model_name = model_cls.__name__

        assert len(set(cqrs_field_names)) == len(cqrs_field_names), \
            'Duplicate names in {} field for model {}.'.format(cqrs_attr, model_name)

    @staticmethod
    def _check_unexisting_names(model_cls, cqrs_field_names, cqrs_attr):
        opts = model_cls._meta
        model_name = model_cls.__name__

        model_field_names = {f.name for f in chain(opts.concrete_fields, opts.private_fields)}
        assert not set(cqrs_field_names) - model_field_names, \
            '{} field is not correctly set for model {}.'.format(cqrs_attr, model_name)

    @staticmethod
    def _check_id_in_names(model_cls, cqrs_field_names, cqrs_attr):
        opts = model_cls._meta
        model_name = model_cls.__name__

        pk_name = opts.pk.name
        assert pk_name in cqrs_field_names, \
            'PK is not in {} for model {}.'.format(cqrs_attr, model_name)
