"""Abstract base models shared by every app."""

from django.db import models

from common.ids import make_id


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PrefixedIDModel(TimeStampedModel):
    """
    Base model with a prefixed string primary key. Subclasses set ``ID_PREFIX``.

    Using CharField(40) keeps indexes compact on MariaDB (utf8mb4 -> 160 bytes).
    """

    ID_PREFIX = "obj"

    id = models.CharField(primary_key=True, max_length=40, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = make_id(self.ID_PREFIX)
        super().save(*args, **kwargs)
