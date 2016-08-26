from __future__ import unicode_literals

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from wagtail.utils.decorators import cached_classmethod
from django.utils.translation import activate, ugettext_lazy
from wagtail.wagtailadmin.forms import WagtailAdminPageForm
from wagtail.wagtailadmin.edit_handlers import (FieldPanel, PageChooserPanel,
                                                MultiFieldPanel, ObjectList,
                                                TabbedInterface)

from wagtail.wagtailcore.models import Page


class Language(models.Model):
    code = models.CharField(
        max_length=12,
        help_text="One of the languages defined in LANGUAGES",
        choices=settings.LANGUAGES,
        unique=True,
    )

    is_default = models.BooleanField(
        default=False, help_text="""
        Visitors with no language preference will see the site in
        this language
        """)

    order = models.IntegerField(
        help_text="""
        Language choices and translations will be displayed in this
        order
        """)

    live = models.BooleanField(
        default=True,
        help_text="Is this language available for visitors to view?"
    )

    def __str__(self):
        return self.code

    class Meta:
        ordering = ['order']

    def verbose(self):
        return [x for x in settings.LANGUAGES if x[0] == self.code][0][1]


def get_default_language():
    return Language.objects.filter(live=True, is_default=True).first()


class AdminTranslatedPageForm(WagtailAdminPageForm):

    def clean_language(self):
        return self.instance.force_parent_language(
            self.parent_page) or get_default_language()


class TranslatedPage(Page):

    canonical_page = models.ForeignKey(
        'self',
        related_name='translations',
        blank=True, null=True, on_delete=models.SET_NULL,
    )

    language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        default=get_default_language
    )

    translation_panels = [
        MultiFieldPanel([
            FieldPanel('language'),
            PageChooserPanel('canonical_page'),
        ])
    ]

    base_form_class = AdminTranslatedPageForm

    def serve(self, request, *args, **kwargs):
        activate(self.language.code)
        return super(TranslatedPage, self).serve(request, *args, **kwargs)

    def save(self, *args, **kwargs):
        super(TranslatedPage, self).save(*args, **kwargs)
        if hasattr(self, 'force_parent_language'):
            self.force_parent_language()

    def get_translations(self, only_live=True):
        if self.canonical_page:
            pages = TranslatedPage.objects.filter(
                Q(canonical_page=self) |
                Q(canonical_page=self.canonical_page) |
                Q(pk=self.canonical_page.pk)
            )
        else:
            pages = TranslatedPage.objects.filter(
                Q(canonical_page=self) |
                Q(pk=self.pk)
            )

        if only_live:
            pages = pages.filter(live=True)
        pages = pages.filter(
            language__live=True
        ).order_by('language__order')
        return pages

    def create_translation(self, language, copy_fields=False):
        if TranslatedPage.objects.filter(
                canonical_page=self,
                language=language).exists():
            raise Exception("Translation already exists")

        model_class = self.content_type.model_class()

        parent = self.get_parent()
        new_parent = parent
        if hasattr(parent, 'get_translations'):
            new_parent = parent.get_translations(only_live=False).filter(
                language=language)

        new_slug = '%s-%s' % (
            self.slug, language.code
        )
        if copy_fields:
            new_page = self.copy(
                update_attrs={
                    'slug': new_slug,
                    'language': language,
                    'live': False,
                    'canonical_page': self,
                }
            )
            if parent != new_parent:
                new_page = new_page.move(new_parent)
        else:
            new_page = model_class(
                slug=new_slug,
                title=self.title,
                language=language,
                live=False,
                canonical_page=self)

            if new_parent:
                new_page = new_parent.add_child(instance=new_page)
            else:
                new_page = self.add_sibling(instance=new_page)

        return new_page

    def force_parent_language(self, parent=None):
        if not parent:
            parent = self.get_parent()
        if parent:
            parent = parent.content_type.get_object_for_this_type(pk=parent.pk)
            if hasattr(parent, 'language'):
                if self.language != parent.language:
                    self.language = parent.language
        return self.language


@cached_classmethod
def get_edit_handler(cls):
    tabs = []
    if cls.content_panels:
        tabs.append(ObjectList(cls.content_panels,
                               heading=ugettext_lazy('Content')))
    if cls.promote_panels:
        tabs.append(ObjectList(cls.promote_panels,
                               heading=ugettext_lazy('Promote')))
    if cls.settings_panels:
        tabs.append(ObjectList(cls.settings_panels,
                               heading=ugettext_lazy('Settings'),
                               classname='settings'))
    if cls.translation_panels:
        tabs.append(ObjectList(cls.translation_panels,
                               heading=ugettext_lazy('Translations')))

    EditHandler = TabbedInterface(tabs, base_form_class=cls.base_form_class)
    return EditHandler.bind_to_model(cls)


TranslatedPage.get_edit_handler = get_edit_handler


def get_user_languages(request):
    if hasattr(request, 'LANGUAGE_CODE'):
        languages = Language.objects.filter(
            code=request.LANGUAGE_CODE)
        if languages.exists():
            return languages
    return Language.objects.filter(is_default=True)


class AbstractTranslationIndexPage(Page):
    def serve(self, request):
        languages = get_user_languages(request)
        candidate_pages = TranslatedPage.objects\
            .live().specific()\
            .child_of(self)
        for language in languages:
            try:
                translation = candidate_pages.filter(
                    language=language).get()
                return redirect(translation.url)
            except TranslatedPage.DoesNotExist:
                continue
        raise Http404

    class Meta:
        abstract = True
