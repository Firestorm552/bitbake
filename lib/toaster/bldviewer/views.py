#
# BitBake Toaster Implementation
#
# Copyright (C) 2013        Intel Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import operator

from django.db.models import Q
from django.shortcuts import render
from orm.models import Build, Target, Task, Layer, Layer_Version, Recipe, Target_Package, LogMessage, Variable
from orm.models import Task_Dependency, Recipe_Dependency, Build_Package, Build_File, Build_Package_Dependency
from django.views.decorators.cache import cache_control

@cache_control(no_store=True)
def build(request):
    template = 'build.html'
    build_info = Build.objects.all()

    logs = LogMessage.objects.all()

    context = {'builds': build_info, 'logs': logs ,
        'hideshowcols' : [
                {'name': 'Output', 'order':10},
                {'name': 'Log', 'order':11},
            ]}

    return render(request, template, context)


def _find_task_revdep(task):
    tp = []
    for p in Task_Dependency.objects.filter(depends_on=task):
        tp.append(p.task);
    return tp

def _find_task_provider(task):
    task_revdeps = _find_task_revdep(task)
    for tr in task_revdeps:
        if tr.outcome != Task.OUTCOME_COVERED:
            return tr
    for tr in task_revdeps:
        trc = _find_task_provider(tr)
        if trc is not None:
            return trc
    return None

def task(request, build_id):
    template = 'task.html'

    tasks = Task.objects.filter(build=build_id)

    for t in tasks:
        if t.outcome == Task.OUTCOME_COVERED:
            t.provider = _find_task_provider(t)

    context = {'build': Build.objects.filter(pk=build_id)[0], 'tasks': tasks}

    return render(request, template, context)

def configuration(request, build_id):
    template = 'configuration.html'
    variables = Variable.objects.filter(build=build_id)
    context = {'build': Build.objects.filter(pk=build_id)[0], 'configuration' : variables}
    return render(request, template, context)

def bpackage(request, build_id):
    template = 'bpackage.html'
    packages = Build_Package.objects.filter(build = build_id)
    context = {'build': Build.objects.filter(pk=build_id)[0], 'packages' : packages}
    return render(request, template, context)

def bfile(request, build_id, package_id):
    template = 'bfile.html'
    files = Build_File.objects.filter(bpackage = package_id)
    context = {'build': Build.objects.filter(pk=build_id)[0], 'files' : files}
    return render(request, template, context)

def tpackage(request, build_id, target_id):
    template = 'package.html'

    packages = Target_Package.objects.filter(target=target_id)

    context = {'build' : Build.objects.filter(pk=build_id)[0],'packages': packages}

    return render(request, template, context)

def layer(request):
    template = 'layer.html'
    layer_info = Layer.objects.all()

    for li in layer_info:
        li.versions = Layer_Version.objects.filter(layer = li)
        for liv in li.versions:
            liv.count = Recipe.objects.filter(layer_version__id = liv.id).count()

    context = {'layers': layer_info}

    return render(request, template, context)


def layer_versions_recipes(request, layerversion_id):
    template = 'recipe.html'
    recipes = Recipe.objects.filter(layer_version__id = layerversion_id)

    context = {'recipes': recipes,
            'layer_version' : Layer_Version.objects.filter( id = layerversion_id )[0]
    }

    return render(request, template, context)

#### API

import json
from django.core import serializers
from django.http import HttpResponse, HttpResponseBadRequest


def model_explorer(request, model_name):

    DESCENDING = 'desc'
    response_data = {}
    model_mapping = {
        'build': Build,
        'target': Target,
        'target_package': Target_Package,
        'task': Task,
        'task_dependency': Task_Dependency,
        'package': Build_Package,
        'layer': Layer,
        'layerversion': Layer_Version,
        'recipe': Recipe,
        'recipe_dependency': Recipe_Dependency,
        'build_package': Build_Package,
        'build_package_dependency': Build_Package_Dependency,
        'build_file': Build_File,
        'variable': Variable,
        'logmessage': LogMessage,
        }

    if model_name not in model_mapping.keys():
        return HttpResponseBadRequest()

    model = model_mapping[model_name]

    try:
        limit = int(request.GET.get('limit', 0))
    except ValueError:
        limit = 0

    try:
        offset = int(request.GET.get('offset', 0))
    except ValueError:
        offset = 0

    ordering_string, invalid = _validate_input(request.GET.get('orderby', ''),
                                               model)
    if invalid:
        return HttpResponseBadRequest()

    filter_string, invalid = _validate_input(request.GET.get('filter', ''),
                                             model)
    if invalid:
        return HttpResponseBadRequest()

    search_term = request.GET.get('search', '')

    if filter_string:
        filter_terms = _get_filtering_terms(filter_string)
        try:
            queryset = model.objects.filter(**filter_terms)
        except ValueError:
            queryset = []
    else:
        queryset = model.objects.all()

    if search_term:
        queryset = _get_search_results(search_term, queryset, model)

    if ordering_string and queryset:
        column, order = ordering_string.split(':')
        if order.lower() == DESCENDING:
            queryset = queryset.order_by('-' + column)
        else:
            queryset = queryset.order_by(column)

    if offset and limit:
        queryset = queryset[offset:(offset+limit)]
    elif offset:
        queryset = queryset[offset:]
    elif limit:
        queryset = queryset[:limit]

    if queryset:
        response_data['count'] = queryset.count()
    else:
        response_data['count'] = 0

    response_data['list'] = serializers.serialize('json', queryset)

    return HttpResponse(json.dumps(response_data),
                        content_type='application/json')

def _get_filtering_terms(filter_string):

    search_terms = filter_string.split(":")
    keys = search_terms[0].split(',')
    values = search_terms[1].split(',')

    return dict(zip(keys, values))

def _validate_input(input, model):

    invalid = 0

    if input:
        input_list = input.split(":")

        # Check we have only one colon
        if len(input_list) != 2:
            invalid = 1
            return None, invalid

        # Check we have an equal number of terms both sides of the colon
        if len(input_list[0].split(',')) != len(input_list[1].split(',')):
            invalid = 1
            return None, invalid

        # Check we are looking for a valid field
        valid_fields = model._meta.get_all_field_names()
        for field in input_list[0].split(','):
            if field not in valid_fields:
                invalid = 1
                return None, invalid

    return input, invalid

def _get_search_results(search_term, queryset, model):
    search_objects = []
    for st in search_term.split(" "):
        q_map = map(lambda x: Q(**{x+'__icontains': st}),
                model.search_allowed_fields)

        search_objects.append(reduce(operator.or_, q_map))
    search_object = reduce(operator.and_, search_objects)
    queryset = queryset.filter(search_object)

    return queryset
