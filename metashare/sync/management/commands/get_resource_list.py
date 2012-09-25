
from django.core.management.base import BaseCommand
from metashare.repository.models import resourceInfoType_model

class Command(BaseCommand):
    def handle(self, *args, **options):
        ext = False
        if 'extended' in args:
            ext = True
        for res in resourceInfoType_model.objects.all():
            sto_obj = res.storage_object
            if sto_obj.published:
                ext_info = ""
                if ext:
                    ext_info = ":{0}".format(sto_obj.source_url)
                print "{1}:{2}{3}".format(res.id, sto_obj.identifier, sto_obj.digest_checksum, ext_info)
        return

