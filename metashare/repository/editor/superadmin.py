'''
Custom base classes for admin interface, for both the top-level admin page
and for inline forms.
'''
import logging
from django.contrib import admin
from django.contrib.admin.util import unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.encoding import force_unicode
from django.utils.html import escape
from django.utils.translation import ugettext as _
from django.db.models.fields.related import ManyToManyField
from django.contrib.admin import helpers
from django.forms.formsets import all_valid
from django.utils.safestring import mark_safe
from django.db import transaction, models
from metashare.repository.supermodel import REQUIRED, RECOMMENDED, \
  OPTIONAL
from metashare import settings
from metashare.repository.editor.related_mixin import RelatedAdminMixin
from metashare.repository.editor.schemamodel_mixin import SchemaModelLookup
from metashare.repository.editor.inlines import ReverseInlineModelAdmin
from metashare.repository.editor.editorutils import is_inline, decode_inline
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

# Setup logging support.
logging.basicConfig(level=settings.LOG_LEVEL)
LOGGER = logging.getLogger('metashare.repository.superadmin')
LOGGER.addHandler(settings.LOG_HANDLER)

csrf_protect_m = method_decorator(csrf_protect)



class SchemaModelAdmin(admin.ModelAdmin, RelatedAdminMixin, SchemaModelLookup):
    '''
    Patched ModelAdmin class. The add_view method is overridden to
    allow the reverse inline formsets to be saved before the parent
    model.
    '''
    no_inlines = []
    custom_one2one_inlines = {}
    inline_type = 'stacked'
    inlines = ()

    class Media:
        js = (settings.MEDIA_URL + 'js/jquery-1.7.1.min.js',
              settings.MEDIA_URL + 'js/addCollapseToAllStackedInlines.js',
              settings.ADMIN_MEDIA_PREFIX + 'js/collapse.min.js',
              settings.ADMIN_MEDIA_PREFIX + 'js/metashare-editor.js',)
    
    def __init__(self, model, admin_site):
        # Get from the model all inlines grouped by Required/Recommended/Optional status:
        self.inlines += tuple(self.get_inline_classes(model, status=REQUIRED) + \
          self.get_inline_classes(model, status=RECOMMENDED) + \
          self.get_inline_classes(model, status=OPTIONAL))
        # Request all many-to-many fields as extended "horizontal filter" widgets:
        self.filter_horizontal = model.get_many_to_many_fields()
        super(SchemaModelAdmin, self).__init__(model, admin_site)    
        # Revers inline code:
        self.no_inlines = self.no_inlines or []
        self.exclude = self.exclude or []
        # Prepare inlines for the required one2one fields:
        for field in model._meta.fields:
            if isinstance(field, models.OneToOneField):
                name = field.name
                if not self.is_required_field(name):
                    self.no_inlines.append(name)
                if not name in self.no_inlines and not name in self.exclude and not name in self.readonly_fields:
                    parent = field.related.parent_model
                    _inline_class = ReverseInlineModelAdmin
                    if  name in self.custom_one2one_inlines:
                        _inline_class = self.custom_one2one_inlines[name]
                    inline = _inline_class(model,
                                           name,
                                           parent,
                                           admin_site,
                                           self.inline_type)
                    self.inline_instances.append(inline)
                    self.exclude.append(name)



    
    def get_fieldsets(self, request, obj=None):
        return SchemaModelLookup.get_fieldsets(self, request, obj)


    def add_to_my_resources(self, obj, user):
        '''
        Add the current user to the list of owners for the current resource,
        thereby adding the resource to the 'my resources' list for the user.
        '''
        if hasattr(obj, 'owners'):
            _field = obj._meta.get_field_by_name('owners')[0]
            if isinstance(_field, ManyToManyField):
                obj.owners.add(user.pk)
                obj.save()
        

    def log_addition(self, request, obj):
        """
        Log that an object has been successfully added and update owners.
        
        We currently update owners information here as save_model() for some
        reason does not properly store the request.user's id in the M2M field.
        """
        self.add_to_my_resources(obj, request.user)
        super(SchemaModelAdmin, self).log_addition(request, obj)

    def log_change(self, request, obj, message):
        """
        Log that an object has been successfully changed and update owners.
        """
        self.add_to_my_resources(obj, request.user)
        super(SchemaModelAdmin, self).log_change(request, obj, message)

        
    def formfield_for_dbfield(self, db_field, **kwargs):
        """
        This is a crucial step in the workflow: for a given db field,
        it is decided how this field will be rendered in the form.
        We have heavily customized this; implementations are in
        RelatedAdminMixin.
        
        Customizations include:
        - hiding certain fields (they are present but invisible);
        - custom widgets for subclassable items such as actorInfo;
        - custom minimalistic "related" widget for non-inlined one2one fields;
        """
        self.hide_hidden_fields(db_field, kwargs)
        if self.is_subclassable(db_field):
            return self.formfield_for_subclassable(db_field, **kwargs)
        self.use_hidden_widget_for_one2one(db_field, kwargs)
        formfield = super(SchemaModelAdmin, self).formfield_for_dbfield(db_field, **kwargs)
        self.use_related_widget_where_appropriate(db_field, kwargs, formfield)
        return formfield

    def response_change(self, request, obj):
        '''
        Response sent after a successful submission of a change form.
        We customize this to allow closing edit popups in the same way
        as response_add deals with add popups.
        '''
        if '_popup' in request.REQUEST:
            return self.edit_response_close_popup_magic(obj)
        else:
            return super(SchemaModelAdmin, self).response_change(request, obj)

    @csrf_protect_m
    @transaction.commit_on_success
    def add_view(self, request, form_url='', extra_context=None):
        """
        The 'add' admin view for this model.
        This follows closely the base implementation from Django 1.3's 
        django.contrib.admin.options.ModelAdmin,
        with the explicitly marked modifications.
        """
        # pylint: disable-msg=C0103
        model = self.model
        opts = model._meta

        if not self.has_add_permission(request):
            raise PermissionDenied

        ModelForm = self.get_form(request)
        formsets = []
        if request.method == 'POST':
            form = ModelForm(request.POST, request.FILES)
            if form.is_valid():
                new_object = self.save_form(request, form, change=False)
                form_validated = True
            else:
                form_validated = False
                new_object = self.model()
            prefixes = {}
            for FormSet, inline in zip(self.get_formsets(request), self.inline_instances):
                #### begin modification ####
                if getattr(FormSet, 'parent_fk_name', None) in self.no_inlines:
                    continue
                #### end modification ####
                prefix = FormSet.get_default_prefix()
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
                if prefixes[prefix] != 1:
                    prefix = "%s-%s" % (prefix, prefixes[prefix])
                formset = FormSet(data=request.POST, files=request.FILES,
                                  instance=new_object,
                                  save_as_new="_saveasnew" in request.POST,
                                  prefix=prefix, queryset=inline.queryset(request))
                formsets.append(formset)
            if all_valid(formsets) and form_validated:
                #### begin modification ####
                for formset, inline in zip(formsets, self.inline_instances):
                    if not isinstance(inline, ReverseInlineModelAdmin):
                        continue
                    saved = formset.save()
                    if saved:
                        obj = saved[0]
                        setattr(new_object, inline.parent_fk_name, obj)
                #### end modification ####
                self.save_model(request, new_object, form, change=False)
                form.save_m2m()
                for formset in formsets:
                    self.save_formset(request, form, formset, change=False)

                self.log_addition(request, new_object)
                return self.response_add(request, new_object)
        else:
            # Prepare the dict of initial data from the request.
            # We have to special-case M2Ms as a list of comma-separated PKs.
            initial = dict(request.GET.items())
            for k in initial:
                try:
                    f = opts.get_field(k)
                except models.FieldDoesNotExist:
                    continue
                if isinstance(f, models.ManyToManyField):
                    initial[k] = initial[k].split(",")
            form = ModelForm(initial=initial)
            prefixes = {}
            for FormSet, inline in zip(self.get_formsets(request),
                                       self.inline_instances):
                #### begin modification ####
                if getattr(FormSet, 'parent_fk_name', None) in self.no_inlines:
                    continue
                #### end modification ####
                prefix = FormSet.get_default_prefix()
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
                if prefixes[prefix] != 1:
                    prefix = "%s-%s" % (prefix, prefixes[prefix])
                formset = FormSet(instance=self.model(), prefix=prefix,
                                  queryset=inline.queryset(request))
                formsets.append(formset)

        #### begin modification ####
        media = self.media or []
        #### end modification ####
        inline_admin_formsets = []
        for inline, formset in zip(self.inline_instances, formsets):
            fieldsets = list(inline.get_fieldsets(request))
            readonly = list(inline.get_readonly_fields(request))
            inline_admin_formset = helpers.InlineAdminFormSet(inline, formset,
                fieldsets, readonly, model_admin=self)
            inline_admin_formsets.append(inline_admin_formset)
            media = media + inline_admin_formset.media

        #### begin modification ####
        adminForm = OrderedAdminForm(form, list(self.get_fieldsets_with_inlines(request)),
            self.prepopulated_fields, self.get_readonly_fields(request),
            model_admin=self, inlines=inline_admin_formsets)
        media = media + adminForm.media
        #### end modification ####

        context = {
            'title': _('Add %s') % force_unicode(opts.verbose_name),
            'adminform': adminForm,
            'is_popup': "_popup" in request.REQUEST,
            'show_delete': False,
            'media': mark_safe(media),
            'inline_admin_formsets': inline_admin_formsets,
            'errors': helpers.AdminErrorList(form, formsets),
            'root_path': self.admin_site.root_path,
            'app_label': opts.app_label,
        }
        context.update(extra_context or {})
        return self.render_change_form(request, context, form_url=form_url, add=True)

    @csrf_protect_m
    @transaction.commit_on_success
    def change_view(self, request, object_id, extra_context=None):
        """
        The 'change' admin view for this model.
        This follows closely the base implementation from Django 1.3's 
        django.contrib.admin.options.ModelAdmin,
        with the explicitly marked modifications.
        """
        # pylint: disable-msg=C0103
        model = self.model
        opts = model._meta

        obj = self.get_object(request, unquote(object_id))

        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        if obj is None:
            raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {'name': force_unicode(opts.verbose_name), 'key': escape(object_id)})

        if request.method == 'POST' and "_saveasnew" in request.POST:
            return self.add_view(request, form_url='../add/')

        ModelForm = self.get_form(request, obj)
        formsets = []
        if request.method == 'POST':
            form = ModelForm(request.POST, request.FILES, instance=obj)
            if form.is_valid():
                form_validated = True
                new_object = self.save_form(request, form, change=True)
            else:
                form_validated = False
                new_object = obj
            prefixes = {}
            for FormSet, inline in zip(self.get_formsets(request, new_object),
                                       self.inline_instances):
                #### begin modification ####
                if getattr(FormSet, 'parent_fk_name', None) in self.no_inlines:
                    continue
                #### end modification ####
                prefix = FormSet.get_default_prefix()
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
                if prefixes[prefix] != 1:
                    prefix = "%s-%s" % (prefix, prefixes[prefix])
                formset = FormSet(request.POST, request.FILES,
                                  instance=new_object, prefix=prefix,
                                  queryset=inline.queryset(request))

                formsets.append(formset)

            if all_valid(formsets) and form_validated:
                self.save_model(request, new_object, form, change=True)
                form.save_m2m()
                for formset in formsets:
                    self.save_formset(request, form, formset, change=True)

                change_message = self.construct_change_message(request, form, formsets)
                self.log_change(request, new_object, change_message)
                return self.response_change(request, new_object)

        else:
            form = ModelForm(instance=obj)
            prefixes = {}
            for FormSet, inline in zip(self.get_formsets(request, obj), self.inline_instances):
                #### begin modification ####
                if getattr(FormSet, 'parent_fk_name', None) in self.no_inlines:
                    continue
                #### end modification ####
                prefix = FormSet.get_default_prefix()
                prefixes[prefix] = prefixes.get(prefix, 0) + 1
                if prefixes[prefix] != 1:
                    prefix = "%s-%s" % (prefix, prefixes[prefix])
                formset = FormSet(instance=obj, prefix=prefix,
                                  queryset=inline.queryset(request))
                formsets.append(formset)

        #### begin modification ####
        media = self.media or []
        #### end modification ####
        inline_admin_formsets = []
        for inline, formset in zip(self.inline_instances, formsets):
            fieldsets = list(inline.get_fieldsets(request, obj))
            readonly = list(inline.get_readonly_fields(request, obj))
            inline_admin_formset = helpers.InlineAdminFormSet(inline, formset,
                fieldsets, readonly, model_admin=self)
            inline_admin_formsets.append(inline_admin_formset)
            media = media + inline_admin_formset.media

        #### begin modification ####
        adminForm = OrderedAdminForm(form, self.get_fieldsets_with_inlines(request, obj),
            self.prepopulated_fields, self.get_readonly_fields(request, obj),
            model_admin=self, inlines=inline_admin_formsets)
        media = media + adminForm.media
        #### end modification ####

        context = {
            'title': _('Change %s') % force_unicode(opts.verbose_name),
            'adminform': adminForm,
            'object_id': object_id,
            'original': obj,
            'is_popup': "_popup" in request.REQUEST,
            'media': mark_safe(media),
            'inline_admin_formsets': inline_admin_formsets,
            'errors': helpers.AdminErrorList(form, formsets),
            'root_path': self.admin_site.root_path,
            'app_label': opts.app_label,
        }
        context.update(extra_context or {})
        return self.render_change_form(request, context, change=True, obj=obj)

class OrderedAdminForm(helpers.AdminForm):
    def __init__(self, form, fieldsets, prepopulated_fields, readonly_fields=None, model_admin=None, inlines=None):
        self.inlines = inlines
        super(OrderedAdminForm, self).__init__(form, fieldsets, prepopulated_fields, readonly_fields, model_admin)
        
    def __iter__(self):
        for name, options in self.fieldsets:
            yield OrderedFieldset(self.form, name,
                readonly_fields=self.readonly_fields,
                model_admin=self.model_admin, inlines=self.inlines,
                **options
            )

class OrderedFieldset(helpers.Fieldset):
    def __init__(self, form, name=None, readonly_fields=(), fields=(), classes=(),
      description=None, model_admin=None, inlines=None):
        self.inlines = inlines
        if not inlines:
            for field in fields:
                if is_inline(field):
                    # an inline is in the field list, but the list of inlines is empty
                    pass
        super(OrderedFieldset, self).__init__(form, name, readonly_fields, fields, classes, description, model_admin)
        
    def __iter__(self):
        for field in self.fields:
            if not is_inline(field):
                fieldline = helpers.Fieldline(self.form, field, self.readonly_fields, model_admin=self.model_admin)
                elem = OrderedElement(fieldline=fieldline)
                yield elem
            else:
                field = decode_inline(field)
                for inline in self.inlines:
                    if hasattr(inline.opts, 'parent_fk_name'):
                        if inline.opts.parent_fk_name == field:
                            elem = OrderedElement(inline=inline)
                            yield elem
                    elif hasattr(inline.formset, 'prefix'):
                        if inline.formset.prefix == field:
                            elem = OrderedElement(inline=inline)
                            yield elem
                    else:
                        raise InlineError('Incorrect inline: no opts.parent_fk_name or formset.prefix found')

class OrderedElement():
    def __init__(self, fieldline=None, inline=None):
        if fieldline:
            self.is_field = True
            self.fieldline = fieldline
        else:
            self.is_field = False
            self.inline = inline
    
            
class InlineError(Exception):
    def __init__(self, msg=None):
        super(InlineError, self).__init__()
        self.msg = msg