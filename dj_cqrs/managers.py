from __future__ import unicode_literals

import logging
from itertools import chain

from django.core.exceptions import ValidationError
from django.db import Error, IntegrityError, transaction
from django.db.models import Manager, F
from django.utils import timezone


logger = logging.getLogger()


class MasterManager(Manager):
    def bulk_update(self, queryset, **kwargs):
        """ Custom update method to support sending update signals.

        :param django.db.models.QuerySet queryset: Django Queryset (f.e. filter)
        :param kwargs: Update kwargs
        """
        with transaction.atomic():
            current_dt = timezone.now()
            result = queryset.update(
                cqrs_revision=F('cqrs_revision') + 1, cqrs_updated=current_dt, **kwargs
            )
        queryset.model.call_post_update(list(queryset.all()))
        return result


class ReplicaManager(Manager):
    @transaction.atomic
    def save_instance(self, master_data):
        """ This method saves (creates or updates) model instance from CQRS master instance data.

        :param dict master_data: CQRS master instance data.
        :return: Model instance.
        :rtype: django.db.models.Model
        """
        mapped_data = self._map_save_data(master_data)

        if mapped_data:
            pk_name = self._get_model_pk_name()
            pk_value = mapped_data[pk_name]
            instance = self.model._default_manager.filter(**{pk_name: pk_value}).first()

            if instance:
                return self.update_instance(instance, mapped_data)

            return self.create_instance(mapped_data)

    def create_instance(self, mapped_data):
        """ This method creates model instance from mapped CQRS master instance data.

        :param dict mapped_data: Mapped CQRS master instance data.
        :return: ReplicaMixin model instance.
        :rtype: django.db.models.Model
        """
        try:
            return self.model._default_manager.create(**mapped_data)
        except (Error, ValidationError) as e:
            pk_value = mapped_data[self._get_model_pk_name()]
            if isinstance(e, IntegrityError):
                logger.warn(
                    'Potentially wrong CQRS sync order: pk = {}, cqrs_revision = {} ({}).'.format(
                        pk_value, mapped_data['cqrs_revision'], self.model.CQRS_ID,
                    ),
                )

            logger.error(
                '{}\nCQRS create error: pk = {} ({}).'.format(
                    str(e), pk_value, self.model.CQRS_ID,
                ),
            )

    def update_instance(self, instance, mapped_data):
        """ This method updates model instance from mapped CQRS master instance data.

        :param django.db.models.Model instance: ReplicaMixin model instance.
        :param dict mapped_data: Mapped CQRS master instance data.
        :return: ReplicaMixin model instance.
        :rtype: django.db.models.Model
        """
        pk_value = mapped_data[self._get_model_pk_name()]
        current_cqrs_revision = mapped_data['cqrs_revision']

        if instance.cqrs_revision > current_cqrs_revision:
            logger.error('Wrong CQRS sync order: pk = {}, cqrs_revision = {} ({}).'.format(
                pk_value, current_cqrs_revision, self.model.CQRS_ID,
            ))
            return

        if instance.cqrs_revision == current_cqrs_revision:
            logger.error('Received duplicate CQRS data: pk = {}, cqrs_revision = {} ({}).'.format(
                pk_value, current_cqrs_revision, self.model.CQRS_ID,
            ))
            if current_cqrs_revision == 0:
                logger.warning(
                    'CQRS potential creation race condition: pk = {} ({}).'.format(
                        pk_value, self.model.CQRS_ID,
                    ),
                )

            return instance

        if current_cqrs_revision != instance.cqrs_revision + 1:
            logger.warn('Lost {} CQRS packages: pk = {}, cqrs_revision = {} ({})'.format(
                current_cqrs_revision - instance.cqrs_revision - 1,
                pk_value, current_cqrs_revision, self.model.CQRS_ID,
            ))

        try:
            for key, value in mapped_data.items():
                setattr(instance, key, value)
            instance.save()
            return instance
        except (Error, ValidationError) as e:
            logger.error(
                '{}\nCQRS update error: pk = {}, cqrs_revision = {} ({}).'.format(
                    str(e), pk_value, current_cqrs_revision, self.model.CQRS_ID,
                ),
            )

    @transaction.atomic
    def delete_instance(self, master_data):
        """ This method deletes model instance from mapped CQRS master instance data.

        :param dict master_data: CQRS master instance data.
        :return: Flag, if delete operation is successful (even if nothing was deleted).
        :rtype: bool
        """
        mapped_data = self._map_delete_data(master_data)

        if mapped_data:
            pk_name = self._get_model_pk_name()
            pk_value = mapped_data[pk_name]
            try:
                self.model._default_manager.filter(**{pk_name: pk_value}).delete()
                return True
            except Error as e:
                logger.error(
                    '{}\nCQRS delete error: pk = {} ({}).'.format(
                        str(e), pk_value, self.model.CQRS_ID,
                    ),
                )

        return False

    def _map_save_data(self, master_data):
        if not self._cqrs_fields_are_filled(master_data):
            return

        mapped_data = self._make_initial_mapping(master_data)
        if not mapped_data:
            return

        if self._get_model_pk_name() not in mapped_data:
            self._log_pk_data_error()
            return

        mapped_data = self._remove_excessive_data(mapped_data)

        if self._all_required_fields_are_filled(mapped_data):
            return mapped_data

    def _make_initial_mapping(self, master_data):
        if self.model.CQRS_MAPPING is None:
            return master_data

        mapped_data = {
            'cqrs_revision': master_data['cqrs_revision'],
            'cqrs_updated': master_data['cqrs_updated'],
        }
        for master_name, replica_name in self.model.CQRS_MAPPING.items():
            if master_name not in master_data:
                logger.error('Bad master-replica mapping for {} ({}).'.format(
                    master_name, self.model.CQRS_ID,
                ))
                return

            mapped_data[replica_name] = master_data[master_name]
        return mapped_data

    def _remove_excessive_data(self, data):
        opts = self.model._meta
        possible_field_names = {f.name for f in chain(opts.concrete_fields, opts.private_fields)}
        return {k: v for k, v in data.items() if k in possible_field_names}

    def _all_required_fields_are_filled(self, mapped_data):
        opts = self.model._meta

        required_field_names = {
            f.name for f in chain(opts.concrete_fields, opts.private_fields) if not f.null
        }
        if not (required_field_names - set(mapped_data.keys())):
            return True

        logger.error(
            'Not all required CQRS fields are provided in data ({}).'.format(self.model.CQRS_ID),
        )
        return False

    def _map_delete_data(self, master_data):
        if 'id' not in master_data:
            self._log_pk_data_error()
            return

        if not self._cqrs_fields_are_filled(master_data):
            return

        return {
            self._get_model_pk_name(): master_data['id'],
            'cqrs_revision': master_data['cqrs_revision'],
            'cqrs_updated': master_data['cqrs_updated'],
        }

    def _cqrs_fields_are_filled(self, data):
        if 'cqrs_revision' in data and 'cqrs_updated' in data:
            return True

        logger.error('CQRS sync fields are not provided in data ({}).'.format(self.model.CQRS_ID))
        return False

    def _log_pk_data_error(self):
        logger.error('CQRS PK is not provided in data ({}).'.format(self.model.CQRS_ID))

    def _get_model_pk_name(self):
        return self.model._meta.pk.name
