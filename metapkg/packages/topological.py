from collections import defaultdict, OrderedDict


class UnresolvedReferenceError(Exception):
    pass


class CycleError(Exception):
    pass


def sort(graph):
    adj = defaultdict(OrderedDict)

    for item_name, item in graph.items():
        for dep in item["deps"]:
            if dep in graph:
                adj[item_name][dep] = True
            else:
                raise UnresolvedReferenceError(
                    'reference to an undefined item {} in {}'.format(
                        dep, item_name))

    visiting = set()
    visited = set()
    sorted = []

    def visit(item):
        if item in visiting:
            raise CycleError("detected cycle on vertex {!r}".format(item))
        if item not in visited:
            visiting.add(item)
            for n in adj[item]:
                visit(n)
            sorted.append(item)
            visiting.remove(item)
            visited.add(item)

    for item in graph:
        visit(item)

    return (graph[item]["item"] for item in sorted)
