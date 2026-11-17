# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Reviewed, bounded projection/filter pipelines; no user code execution."""
import json
import math
import re
from s2_platform import S2Error, Conflict, NotFound, canonical, digest, new_id, now, dominates
from s2_ontology_kernel.bindings import joined


class Pipelines:
    def __init__(self, store, security, kernel):
        self.store, self.security, self.kernel = store, security, kernel

    def _dataset(self, db, ctx, manifest, name):
        value = self.kernel.resource(db, ctx, manifest, 'dataset/' + name)
        self.kernel.check_definition_access(ctx, value['schema'])
        if len(value['rows']) > 1000: raise S2Error('Pipeline snapshots are limited to 1000 rows')
        for row in value['rows']: self.security.check_marking(ctx, row['marking'])
        return value

    def _spec(self, definition, source, target):
        if not isinstance(definition, dict) or set(definition) != {'input','output','mappings','filter','expectations'}:
            raise S2Error('Explicit input/output/mappings/filter/expectations required')
        if definition['input'] == definition['output']: raise Conflict('Input and output must differ')
        mappings = definition['mappings']
        if not isinstance(mappings, dict) or not mappings: raise S2Error('Projection mappings required')
        for dest, src in mappings.items():
            if not isinstance(src, str) or dest not in target['schema']['properties'] or src not in source['schema']['properties']:
                raise S2Error('Unknown projection property')
            if target['schema']['properties'][dest]['type'] != source['schema']['properties'][src]['type']:
                raise S2Error('Projection requires matching scalar types')
        if mappings.get(target['primary_key']) != source['primary_key']:
            raise S2Error('Projection must preserve primary key identity')
        if any(spec.get('required') and prop not in mappings for prop,spec in target['schema']['properties'].items()):
            raise S2Error('Required output properties must be mapped')
        predicate = definition['filter']
        if predicate is not None:
            if not isinstance(predicate, dict) or set(predicate) != {'property','equals'}:
                raise S2Error('Only a typed equality filter is supported')
            prop = predicate['property']
            if not isinstance(prop,str) or prop not in source['schema']['properties']: raise S2Error('Unknown filter property')
            self.kernel.validate_values({'properties': {prop:source['schema']['properties'][prop]}}, {prop:predicate['equals']})
        checks = definition['expectations']
        if not isinstance(checks,dict) or set(checks) != {'min_rows','max_rows','ranges'}:
            raise S2Error('Explicit min_rows/max_rows/ranges required')
        if any(type(checks[k]) is not int for k in ('min_rows','max_rows')) or not 0 <= checks['min_rows'] <= checks['max_rows'] <= 1000:
            raise S2Error('Invalid row-count limits')
        if not isinstance(checks['ranges'],dict): raise S2Error('Ranges must be a mapping')
        for prop, limits in checks['ranges'].items():
            if prop not in mappings or target['schema']['properties'][prop]['type'] not in {'number','integer'}:
                raise S2Error('Range requires a mapped numeric property')
            if not isinstance(limits,list) or len(limits)!=2 or any(type(v) not in (int,float) for v in limits):
                raise S2Error('Range requires two finite numbers')
            try: valid = all(math.isfinite(v) for v in limits) and limits[0] <= limits[1]
            except OverflowError: valid = False
            if not valid: raise S2Error('Invalid range')

    def define(self, db, ctx, branch, expected_head, pipeline, definition):
        if not isinstance(pipeline,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',pipeline): raise S2Error('Invalid pipeline name')
        if not isinstance(definition,dict) or any(not isinstance(definition.get(k),str) for k in ('input','output')):
            raise S2Error('Dataset references required')
        manifest = self.store.manifest(db,ctx.tenant,expected_head)
        source = self._dataset(db,ctx,manifest,definition['input'])
        target = self._dataset(db,ctx,manifest,definition['output'])
        self._spec(definition,source,target)
        if 'dataset/'+definition['output'] in source.get('source_pipeline',{}).get('policy_resources',[]):
            raise Conflict('Pipeline provenance cycle rejected')
        key='pipeline/'+pipeline
        label=joined(source['schema'].get('marking'),target['schema'].get('marking'))
        if key in manifest:
            previous=self.kernel.resource(db,ctx,manifest,key)
            self.security.check_marking(ctx,previous['marking'])
            if (previous['input'],previous['output']) != (definition['input'],definition['output']):
                raise Conflict('Changing pipeline dataset identity requires a new pipeline')
            label=joined(label,previous['marking'])
        label=self.security.effective(db,ctx,key,label)
        self.security.check_marking(ctx,label)
        payload={**definition,'marking':label,
                 'input_schema':digest(self.store.revision(db,ctx.tenant,manifest['dataset/'+definition['input']])['schema']),
                 'output_schema':digest(self.store.revision(db,ctx.tenant,manifest['dataset/'+definition['output']])['schema'])}
        manifest[key]=self.store.put_revision(db,ctx.tenant,'pipeline',payload)
        # Old runtimes must refuse catalogs with live derived-dataset policy semantics.
        db.execute('UPDATE schema_version SET version=6 WHERE version<6')
        return {'commit':self.store.publish(db,ctx,branch,expected_head,manifest),'revision':manifest[key]}

    def _plan(self,db,ctx,branch,expected_head,pipeline):
        if self.store.branch(db,ctx.tenant,branch)['head'] != expected_head: raise Conflict('Stale branch head')
        manifest=self.store.manifest(db,ctx.tenant,expected_head)
        spec=self.kernel.resource(db,ctx,manifest,'pipeline/'+pipeline)
        self.security.check_marking(ctx,spec['marking'])
        source=self._dataset(db,ctx,manifest,spec['input']);target=self._dataset(db,ctx,manifest,spec['output'])
        self._spec({k:spec[k] for k in ('input','output','mappings','filter','expectations')},source,target)
        for kind,name in [('input',spec['input']),('output',spec['output'])]:
            raw=self.store.revision(db,ctx.tenant,manifest['dataset/'+name])
            if digest(raw['schema']) != spec[kind+'_schema']: raise Conflict('Schema changed; redefine pipeline explicitly')
        dependencies=set(source.get('source_pipeline',{}).get('policy_resources',[]))
        dependencies.update(target.get('source_pipeline',{}).get('policy_resources',[]))
        if 'dataset/'+spec['output'] in source.get('source_pipeline',{}).get('policy_resources',[]):
            raise Conflict('Pipeline provenance cycle rejected')
        dependencies.update(['pipeline/'+pipeline,'dataset/'+spec['input']])
        dependencies.update('dataset/'+spec['input']+'/property/'+p for p in source['schema']['properties'])
        dependencies.update('dataset/'+spec['input']+'/row/'+canonical(r['values'][source['primary_key']]) for r in source['rows'])
        label=joined(spec['marking'],source['schema'].get('marking'),target['schema'].get('marking'),
                     *(p.get('marking') for p in source['schema']['properties'].values()),
                     *(r['marking'] for r in source['rows']),*(r['marking'] for r in target['rows']))
        for dep in dependencies: label=joined(label,self.security.effective(db,ctx,dep,None))
        self.security.check_marking(ctx,label)
        rows=[];errors=[]
        for index,row in enumerate(source['rows'],1):
            values=row['values'];pred=spec['filter']
            if pred is not None and (pred['property'] not in values or canonical(values[pred['property']])!=canonical(pred['equals'])): continue
            projected={dest:values[src] for dest,src in spec['mappings'].items() if src in values}
            try:self.kernel.validate_values(target['schema'],projected)
            except (S2Error,OverflowError):
                errors.append({'record':index,'code':'OUTPUT_SCHEMA'});continue
            for prop,(lo,hi) in spec['expectations']['ranges'].items():
                if prop not in projected or not lo <= projected[prop] <= hi: errors.append({'record':index,'code':'RANGE','property':prop})
            key=canonical(projected[target['primary_key']])
            effective=joined(label,self.security.effective(db,ctx,'dataset/'+spec['output']+'/row/'+key,None))
            self.security.check_marking(ctx,effective)
            rows.append({'values':projected,'marking':effective})
        if not spec['expectations']['min_rows'] <= len(rows) <= spec['expectations']['max_rows']:
            errors.append({'code':'ROW_COUNT'})
        plan={'branch':branch,'pipeline':pipeline,'base_commit':expected_head,'pipeline_revision':manifest['pipeline/'+pipeline],
              'input':spec['input'],'input_revision':manifest['dataset/'+spec['input']],
              'output':spec['output'],'previous_output_revision':manifest['dataset/'+spec['output']],
              'input_rows':len(source['rows']),'previous_output_rows':len(target['rows']),'output_rows':len(rows),
              'errors':errors,'can_publish':not errors,'operation':'REPLACE_SNAPSHOT'}
        provenance={'pipeline':pipeline,'pipeline_revision':plan['pipeline_revision'],'input':spec['input'],
                    'input_revision':plan['input_revision'],'base_commit':expected_head,'policy_resources':sorted(dependencies),'marking':label}
        # Retain declared schema, not its effective-policy expansion, to avoid false schema drift.
        raw=self.store.revision(db,ctx.tenant,plan['previous_output_revision'])
        snapshot={**raw,'rows':rows,'parent':plan['previous_output_revision'],'source_pipeline':provenance}
        plan['plan_hash']=digest({'plan':plan,'snapshot':snapshot,'source':source,'target':target,'actor':ctx.principal})
        return plan,snapshot,manifest

    def preview(self,db,ctx,branch,expected_head,pipeline):
        return self._plan(db,ctx,branch,expected_head,pipeline)[0]

    def run(self,db,ctx,branch,expected_head,pipeline,plan_hash,idempotency_key):
        if not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=200:raise S2Error('Bounded idempotency key required')
        request=digest({'actor':ctx.principal,'base':expected_head,'pipeline':pipeline,'plan_hash':plan_hash})
        replay=db.execute('SELECT result,request_hash FROM pipeline_runs WHERE tenant=? AND branch=? AND idem=?',(ctx.tenant,branch,idempotency_key)).fetchone()
        if replay:
            if replay['request_hash']!=request:raise Conflict('Idempotency key reused')
            return self._receipt(db,ctx,json.loads(replay['result']))
        plan,snapshot,manifest=self._plan(db,ctx,branch,expected_head,pipeline)
        if plan_hash!=plan['plan_hash']:raise Conflict('Plan or policy changed; preview again')
        if not plan['can_publish']:raise Conflict('Data health expectations failed')
        revision=self.store.put_revision(db,ctx.tenant,'dataset_snapshot',snapshot)
        manifest['dataset/'+plan['output']]=revision
        result={**plan,'snapshot':revision,'commit':self.store.publish(db,ctx,branch,expected_head,manifest),
                'run':new_id(),'status':'COMMITTED','actor':ctx.principal,'created_at':now()}
        db.execute('INSERT INTO pipeline_runs VALUES(?,?,?,?,?,?,?)',(ctx.tenant,branch,result['run'],ctx.principal,idempotency_key,request,canonical(result)))
        return result

    def _receipt(self,db,ctx,result):
        manifest=self.store.manifest(db,ctx.tenant,result['commit'])
        spec=self.kernel.resource(db,ctx,manifest,'pipeline/'+result['pipeline'])
        self.security.check_marking(ctx,spec['marking'])
        self._dataset(db,ctx,manifest,result['input']);self._dataset(db,ctx,manifest,result['output'])
        return result

    def get(self,db,ctx,branch,run):
        row=db.execute('SELECT result FROM pipeline_runs WHERE tenant=? AND branch=? AND id=? AND actor=?',(ctx.tenant,branch,run,ctx.principal)).fetchone()
        if row is None:raise NotFound('Pipeline run not found')
        return self._receipt(db,ctx,json.loads(row['result']))

    def validate_merged_manifest(self, db, ctx, manifest):
        """Read-only consistency proof; never rebuild datasets during a merge."""
        definitions = {key[9:]: self.kernel.resource(db, ctx, manifest, key)
                       for key in manifest if key.startswith('pipeline/')}
        if len(definitions) > 100:
            raise S2Error('Reviewed merge is limited to 100 pipeline definitions')
        producers = {}
        for name, spec in definitions.items():
            self.security.check_marking(ctx, spec['marking'])
            if spec['output'] in producers:
                raise Conflict('Merged pipelines have competing output writers')
            producers[spec['output']] = name
        pending = set(definitions)
        ordered = []
        while pending:
            ready = sorted(name for name in pending
                           if producers.get(definitions[name]['input']) not in pending)
            if not ready:
                raise Conflict('Merged pipeline dependency graph contains a cycle')
            ordered.extend(ready)
            pending.difference_update(ready)
        for key in manifest:
            if key.startswith('dataset/'):
                raw = self.store.revision(db, ctx.tenant, manifest[key])
                provenance = raw.get('source_pipeline')
                if provenance and (provenance['pipeline'] not in definitions or
                        definitions[provenance['pipeline']]['output'] != key[8:]):
                    raise Conflict('Derived dataset has no matching pipeline definition')
        proofs = []
        for name in ordered:
            spec = definitions[name]
            source = self._dataset(db, ctx, manifest, spec['input'])
            target = self._dataset(db, ctx, manifest, spec['output'])
            self._spec({k: spec[k] for k in ('input','output','mappings','filter','expectations')}, source, target)
            raw_source = self.store.revision(db, ctx.tenant, manifest['dataset/' + spec['input']])
            raw_target = self.store.revision(db, ctx.tenant, manifest['dataset/' + spec['output']])
            if digest(raw_source['schema']) != spec['input_schema'] or digest(raw_target['schema']) != spec['output_schema']:
                raise Conflict('Merged pipeline schema pins are stale')
            provenance = raw_target.get('source_pipeline', {})
            if (provenance.get('pipeline') != name or provenance.get('pipeline_revision') != manifest['pipeline/' + name]
                    or provenance.get('input') != spec['input'] or provenance.get('input_revision') != manifest['dataset/' + spec['input']]):
                raise Conflict('Merged pipeline output is missing or stale; run before merge')
            expected = []
            for row in source['rows']:
                values = row['values']; predicate = spec['filter']
                if predicate is not None and (predicate['property'] not in values or
                        canonical(values[predicate['property']]) != canonical(predicate['equals'])):
                    continue
                projected = {dest: values[src] for dest, src in spec['mappings'].items() if src in values}
                self.kernel.validate_values(target['schema'], projected)
                for prop, (lo, hi) in spec['expectations']['ranges'].items():
                    if prop not in projected or not lo <= projected[prop] <= hi:
                        raise Conflict('Merged pipeline fails range expectations')
                expected.append(projected)
            if not spec['expectations']['min_rows'] <= len(expected) <= spec['expectations']['max_rows']:
                raise Conflict('Merged pipeline fails row-count expectations')
            if sorted(map(canonical, expected)) != sorted(canonical(row['values']) for row in target['rows']):
                raise Conflict('Merged output differs from its declared transform; rerun before merge')
            dependencies = set(raw_source.get('source_pipeline', {}).get('policy_resources', []))
            dependencies.update(['pipeline/' + name, 'dataset/' + spec['input']])
            dependencies.update('dataset/' + spec['input'] + '/property/' + prop for prop in source['schema']['properties'])
            dependencies.update('dataset/' + spec['input'] + '/row/' + canonical(row['values'][source['primary_key']])
                                for row in source['rows'])
            if not dependencies.issubset(provenance.get('policy_resources', [])):
                raise Conflict('Merged output is missing source policy dependencies')
            raw_spec = self.store.revision(db, ctx.tenant, manifest['pipeline/' + name])
            floor = joined(raw_spec.get('marking'), raw_source['schema'].get('marking'),
                           raw_source.get('source_pipeline', {}).get('marking'),
                           *(p.get('marking') for p in raw_source['schema']['properties'].values()),
                           *(r.get('marking') for r in raw_source['rows']))
            if not dominates(joined(provenance.get('marking')), floor):
                raise Conflict('Merged output classification is below its source')
            proofs.append({'pipeline': name, 'pipeline_revision': manifest['pipeline/' + name],
                           'input_revision': manifest['dataset/' + spec['input']],
                           'output_revision': manifest['dataset/' + spec['output']],
                           'output_rows': len(expected), 'health': 'PASSED'})
        return proofs
