!/usr/bin/env python3
"""
extract_full_symbol_table.py

Usage:
    python3 extract_full_symbol_table.py /path/to/your.owl [out.json]

Produces a detailed JSON symbol table with:
 - classes, object/datatype/annotation properties, individuals
 - labels/comments/altLabels and all annotation triples
 - explicit domain/range for properties
 - subClassOf / equivalentClass with traversal of anonymous class expressions:
     - owl:Restriction (onProperty, someValuesFrom, allValuesFrom, hasValue, cardinalities)
     - ObjectIntersectionOf / ObjectUnionOf (rdf:List)
     - owl:complementOf, owl:oneOf (enumeration)
 - property characteristics (FunctionalProperty, TransitiveProperty, etc.)
"""

import sys, json
from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, RDFS, OWL, SKOS
from rdflib.collection import Collection

def fq(node):
    return str(node) if node is not None else None

def get_literals_for_pred(g, subject, preds):
    for p in preds:
        for o in g.objects(subject, p):
            if isinstance(o, Literal):
                yield str(o)

def collect_annotations(g, subject):
    ann = {}
    for p,o in g.predicate_objects(subject):
        if p in (RDF.type, RDFS.subClassOf, OWL.equivalentClass, RDFS.domain, RDFS.range, RDFS.subPropertyOf):
            continue
        ann.setdefault(str(p), []).append(fq(o))
    return ann

def rdf_list_to_py(g, node):
    """Convert an RDF list (rdf:first/rdf:rest) starting at node into a Python list of objects (IRIs/BNodes/Literals)."""
    items = []
    try:
        coll = Collection(g, node)
        for it in coll:
            items.append(it)
    except Exception:
        cur = node
        while cur and cur != RDF.nil:
            first = g.value(cur, RDF.first)
            if first is None:
                break
            items.append(first)
            cur = g.value(cur, RDF.rest)
            if cur is None:
                break
    return items

def summarize_expression(g, node):
    """
    Return a human-friendly dict describing a class expression or node.
    Handles:
      - URIRef: returns { "type":"Named", "iri":... }
      - BNode that is an owl:Restriction -> dict describing restriction
      - BNode for intersectionOf/unionOf -> list of summarized operands
      - complementOf, oneOf, etc.
    """
    if isinstance(node, URIRef):
        return {"exprType":"Named", "iri": str(node)}
    if isinstance(node, Literal):
        return {"exprType":"Literal", "value": str(node)}
    if not isinstance(node, BNode):
        return {"exprType":"Unknown", "value": fq(node)}

    if (node, RDF.type, OWL.Restriction) in g or g.value(node, OWL.onProperty) is not None:
        on_prop = g.value(node, OWL.onProperty)
        res = {"exprType":"Restriction", "onProperty": fq(on_prop)}
        for pred, key in [(OWL.someValuesFrom, "someValuesFrom"),
                          (OWL.allValuesFrom, "allValuesFrom"),
                          (OWL.hasValue, "hasValue"),
                          (OWL.minQualifiedCardinality, "minQualifiedCardinality"),
                          (OWL.maxQualifiedCardinality, "maxQualifiedCardinality"),
                          (OWL.qualifiedCardinality, "qualifiedCardinality"),
                          (OWL.minCardinality, "minCardinality"),
                          (OWL.maxCardinality, "maxCardinality"),
                          (OWL.cardinality, "cardinality")]:
            val = g.value(node, pred)
            if val is not None:
                if isinstance(val, (BNode, URIRef)):
                    res[key] = summarize_expression(g, val)
                else:
                    res[key] = fq(val)
        return res

    inter = g.value(node, OWL.intersectionOf)
    if inter is not None:
        items = rdf_list_to_py(g, inter)
        return {"exprType":"Intersection", "operands":[summarize_expression(g,i) for i in items]}

    union = g.value(node, OWL.unionOf)
    if union is not None:
        items = rdf_list_to_py(g, union)
        return {"exprType":"Union", "operands":[summarize_expression(g,i) for i in items]}

    comp = g.value(node, OWL.complementOf)
    if comp is not None:
        return {"exprType":"Complement", "operand": summarize_expression(g, comp)}

    oneof = g.value(node, OWL.oneOf)
    if oneof is not None:
        items = rdf_list_to_py(g, oneof)
        return {"exprType":"OneOf", "members":[summarize_expression(g,i) for i in items]}

    props = {}
    for p,o in g.predicate_objects(node):
        props.setdefault(str(p), []).append(fq(o))
    return {"exprType":"BNode", "props": props}

def extract_property_characteristics(g, prop):
    chars = []
    if (prop, RDF.type, OWL.FunctionalProperty) in g:
        chars.append("Functional")
    if (prop, RDF.type, OWL.InverseFunctionalProperty) in g:
        chars.append("InverseFunctional")
    if (prop, RDF.type, OWL.TransitiveProperty) in g:
        chars.append("Transitive")
    if (prop, RDF.type, OWL.SymmetricProperty) in g:
        chars.append("Symmetric")
    if (prop, RDF.type, OWL.AsymmetricProperty) in g:
        chars.append("Asymmetric")
    if (prop, RDF.type, OWL.ReflexiveProperty) in g:
        chars.append("Reflexive")
    if (prop, RDF.type, OWL.IrreflexiveProperty) in g:
        chars.append("Irreflexive")
    return chars

def gather_terms(g):
    terms = []
    categories = [
        ("Class", OWL.Class),
        ("ObjectProperty", OWL.ObjectProperty),
        ("DatatypeProperty", OWL.DatatypeProperty),
        ("AnnotationProperty", OWL.AnnotationProperty),
        ("Individual", RDF.type)  
    ]

    for s in set(g.subjects(RDF.type, OWL.Class)):
        if isinstance(s, URIRef):
            terms.append(("Class", s))
    for subj in set(g.subjects()):
        if isinstance(subj, URIRef) and ( (subj, RDFS.subClassOf, None) in g or (subj, OWL.equivalentClass, None) in g):
            if not any(t[1]==subj for t in terms):
                terms.append(("Class", subj))

    for s in set(g.subjects(RDF.type, OWL.ObjectProperty)):
        if isinstance(s, URIRef):
            terms.append(("ObjectProperty", s))
    for s in set(g.subjects(RDF.type, OWL.DatatypeProperty)):
        if isinstance(s, URIRef):
            terms.append(("DatatypeProperty", s))
    for s in set(g.subjects(RDF.type, OWL.AnnotationProperty)):
        if isinstance(s, URIRef):
            terms.append(("AnnotationProperty", s))

    for s in set(g.subjects(RDF.type, None)):
        if isinstance(s, URIRef):
            if any(s == t[1] for t in terms):
                continue
            types = list(g.objects(s, RDF.type))
            if types:
                terms.append(("Individual", s))

 
    seen = {}
    symbol_entries = {}

    for cat, node in terms:
        iri = str(node)
        entry = symbol_entries.get(iri, {"iri": iri, "types": [], "labels": [], "altLabels": [], "comments": [], "annotations": {}, "domains": [], "ranges": [], "subClassOf": [], "equivalentClass": [], "propertyCharacteristics": [], "subPropertyOf": [], "inverseOf": [], "individualTypes": []})
        if cat not in entry["types"]:
            entry["types"].append(cat)

        for lab in get_literals_for_pred(g, node, [RDFS.label, SKOS.prefLabel]):
            if lab not in entry["labels"]:
                entry["labels"].append(lab)
        for alt in get_literals_for_pred(g, node, [SKOS.altLabel]):
            if alt not in entry["altLabels"]:
                entry["altLabels"].append(alt)
        for com in get_literals_for_pred(g, node, [RDFS.comment]):
            if com not in entry["comments"]:
                entry["comments"].append(com)
      
        ann = collect_annotations(g, node)
      
        for k,v in ann.items():
            lst = entry["annotations"].setdefault(k, [])
            for item in v:
                if item not in lst:
                    lst.append(item)

      
        if cat in ("ObjectProperty", "DatatypeProperty", "AnnotationProperty"):
            domains = [str(o) for o in g.objects(node, RDFS.domain)]
            ranges = [str(o) for o in g.objects(node, RDFS.range)]
            for d in domains:
                if d not in entry["domains"]:
                    entry["domains"].append(d)
            for r in ranges:
                if r not in entry["ranges"]:
                    entry["ranges"].append(r)
            # subPropertyOf
            for sp in g.objects(node, RDFS.subPropertyOf):
                if str(sp) not in entry["subPropertyOf"]:
                    entry["subPropertyOf"].append(str(sp))
            # inverseOf
            for inv in g.objects(node, OWL.inverseOf):
                if str(inv) not in entry["inverseOf"]:
                    entry["inverseOf"].append(str(inv))
            # characteristics
            entry["propertyCharacteristics"] = extract_property_characteristics(g, node)

        # classes: subclass/equivalent class expressions
        if cat == "Class":
            # direct superclass IRIs
            for sup in g.objects(node, RDFS.subClassOf):
                # sup may be URIRef or BNode (anonymous expr)
                summary = summarize_expression(g, sup)
                entry["subClassOf"].append(summary)
            for eq in g.objects(node, OWL.equivalentClass):
                entry["equivalentClass"].append(summarize_expression(g, eq))

        # individuals: record their rdf:types
        if cat == "Individual":
            for t in g.objects(node, RDF.type):
                entry["individualTypes"].append(str(t))

        symbol_entries[iri] = entry

  
    result_terms = list(symbol_entries.values())
    return result_terms

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_full_symbol_table.py /path/to/ontology.ttl [out.json]")
        sys.exit(1)
    infile = sys.argv[1]
    outfile = sys.argv[2] if len(sys.argv) > 2 else "symbol_table_full.json"
    g = Graph()
    print("Parsing:", infile)
    g.parse(infile)  
    print("Graph parsed. Triples:", len(g))
    terms = gather_terms(g)
    out = {
        "sourceFile": infile,
        "termCount": len(terms),
        "terms": terms
    }
    with open(outfile, "w", encoding="utf8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("Wrote", outfile, "with", len(terms), "term entries.")

if __name__ == "__main__":
    main()
