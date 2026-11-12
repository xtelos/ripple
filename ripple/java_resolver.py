"""What each call site of a Java repo is, decided from the declarations and never guessed.

A call is an edge only when the source proves it. Everything else is an unresolved call with its reason, or a call
to code outside the repo, and one wrong edge is worse than a hundred of those. What is provable, and the rule that
makes it safe (the repo compiles, so a call has a target and the declared types are the real ones):

  names      A type name is a type variable, then a type of an enclosing class (or one it inherits), a local class,
             a type of the file, a single-type import, a type of the same package, an on-demand import of a repo
             package or type, then java.lang. Two on-demand repo matches, or a name nothing supplies, is unresolved.
             A variable hides a type of the same name (JLS 6.4.2), so `x.m()` with a local or field `x` is a call on it,
             and so does a field a static import supplies (a single import, or an on-demand one of a type outside the
             repo, which may supply any name: then the name is not decided). In `T.f` a field f of T is named before a
             member type f. The extends clause of a class is outside its body (its own member types are not in scope
             there). A local class is in scope from its declaration to the end of its block: a name written outside
             that is not decided; inside a local or anonymous class the member types it declares or inherits come
             before the local classes of the method around it. A member type inherited from a supertype outside the
             repo hides everything further out, so with such a supertype (one not in KNOWN_SUPERTYPES) a name its repo
             members do not supply is unresolved.
  receivers  `this`, `super`, `new T()`, a cast, a parameter, local, field or `var` with a written or initializer type,
             a chain of calls through each method's declared return type, a pattern variable, a string or number
             literal. Generics are erased (`List<Item>.get(0)` is a List's call), so a type variable, an array
             element, a conditional, an untyped lambda parameter and the result of a call into code outside the repo
             are unresolved. A pattern variable (an `instanceof` binding, a type pattern, a component of a record
             pattern, nested ones too, seen inside a lambda as well) that is also a field is unresolved (its scope is
             by flow), and so is a field of a supertype that is not a member (private, or package-private in another
             package). A comment is no node of the code: it can stand between any two tokens, and none of these rules
             counts, indexes or compares one (java_indexer.named and text).
  comb rule  `m(args)` with no receiver is searched in the innermost enclosing class whose hierarchy has a method
             named m, whatever the arity (JLS 15.12.1): an inner class's `m()` is the outer class's only when the
             inner class and everything it inherits has no m. A static import comes after, and two imported
             owners of one name are not decided.
  overloads  Candidates are every method of the name, applicable by arity, in the receiver's type and ALL its repo
             supertypes, that the type has as members (a private method is not inherited, nor a package-private one
             in another package) and the caller may call (JLS 6.6). A declaration an override of one in a subtype is
             dropped, so is an interface's default one beaten by a class's. One candidate is the edge. Several
             are an edge only when every argument's type is known and exactly one candidate's parameter types are
             those, which is always the most specific (JLS 15.12.2), and a parameter with type arguments takes only an
             argument written with the same ones. Boxing, widening, subtyping and inference never
             pick: the call is unresolved, "N overloads of m take n arguments". A constructor is chosen the same way,
             and a record's canonical constructor is a candidate beside any it writes unless it writes it itself.
  dispatch   A call on a repo type that is not static, private or final, on a type with subtypes, also gets "override"
             edges to the methods that override it in every repo subtype, anonymous classes and enum constant bodies
             included: the same name and the parameter types the method has as the subtype sees it (`visit(Item)` for
             `Visitor<Item>.visit(T)` in `class V implements Visitor<Item>`; a type variable is what the supertype
             clause makes of it, never "any type"), and the one a subtype inherits from a superclass that is not under
             the receiver's type (`class B extends A implements I` runs A.m for `I.m()`). Not for `super.m()`,
             `Type.m()` or `new T().m()`, which are exact.
  strict     A supertype from outside the repo may declare another method of the name, and with no classpath that is
             not known: a call that could be answered by one is unresolved, "an external supertype may declare
             another m". The exceptions are the supertypes whose members are all known (KNOWN_MEMBERS: Object, Enum
             and Record, which the language puts under every class, enum and record, and every type of
             KNOWN_SUPERTYPES, from the JDK's own declarations): one that lacks the name cannot declare it, a marker
             interface such as Serializable or Cloneable declares nothing, and the reason is only given for a name
             such a type, or one that is not known, may declare. This was measured against javac's bytecode: the
             lenient rule (ignore them) made a wrong edge, `Ledger.write(Item)` for a call javac sends to
             java.io.Writer.write(String).
  outside    A call counts as outside the repo only when it provably cannot run repo code. A value typed with an
             external class or interface (or Object) may be an instance of a repo type that has that supertype, so a
             call of a name such a type declares is unresolved (`List l = new MyList(); l.add(x)`), unless the JDK
             class is final (FINAL_EXTERNAL): the class that declares the method, and every subtype of it that
             inherits it (`class Task extends RunBase implements Runnable {}` runs RunBase.run for `Runnable.run()`),
             are asked. A method no repo class above the receiver declares is outside the repo only if no subtype of
             the receiver declares it either.
  absence    A method name no repo source declares with that arity cannot run repo code, so a call of it that is
             otherwise unresolved counts as outside the repo. Off in a repo that uses Lombok, which writes methods
             no source holds. A method whose header the parser could not read (a type annotation on a varargs
             parameter) takes any number of arguments, and a call of its name is not decided.

A call into the JDK or a library is counted as external, with a reason. A method reference is not a call and is
never an edge. A lambda's calls belong to the method around it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .java_indexer import (
    CALLABLES,
    CONSTRUCTOR,
    LOMBOK,
    PRIMITIVES,
    TYPE_DECLS,
    Declarations,
    JavaMethod,
    JavaType,
    line_of,
    named,
    strip_type,
    text,
)
from .model import Edge, Unresolved
from .profile import LanguageProfile

OBJECT = "java.lang.Object"

# The members of the three supertypes the language itself puts under every class, enum and record (JLS 8.1.4, 8.9.3,
# 8.10.3), read off java.lang.Object, java.lang.Enum and java.lang.Record with `javap -protected` on JDK 21. Nothing
# else about the JDK is known, and no other external type is trusted to lack a name.
#
# Object's methods with the parameter types of each arity. A repo method of the same name and arity either repeats
# these (it overrides Object's) or does not (an overload that a call of Object's may be meant for).
OBJECT_SIGNATURES: dict[tuple[str, int], tuple[str, ...]] = {
    ("equals", 1): (OBJECT,), ("wait", 1): ("long",), ("wait", 2): ("long", "int"),
    **{(name, 0): () for name in ("getClass", "hashCode", "clone", "toString", "notify", "notifyAll", "wait", "finalize")},
}  # fmt: skip
OBJECT_MEMBERS = frozenset(name for name, _ in OBJECT_SIGNATURES)
# What java.lang.Enum adds to Object's (any arity: `valueOf` is declared with one and two parameters, and a repo
# enum may overload `valueOf` or `compareTo`). java.lang.Record adds nothing: its three methods are Object's.
ENUM_MEMBERS = frozenset({"name", "ordinal", "compareTo", "getDeclaringClass", "describeConstable", "values", "valueOf"})
# The supertypes outside the repo whose members are known: member name -> the numbers of parameters it is declared with
# (ANY: not known, which is any number). A name, or a number of arguments, that is not in the entry is not that type's, so
# a call of it is not one that such a supertype could answer. Enum and Record are put under every enum and record by the
# language; every entry of KNOWN_SUPERTYPES below is known the same way (what the type and everything above it
# declares). A marker interface (Serializable, Cloneable, RandomAccess, EventListener) declares nothing at all.
ANY = (-1,)
KNOWN_MEMBERS: dict[str, dict[str, tuple[int, ...]]] = {"java.lang.Record": {}}

# The well known JDK types whose whole supertype list is known, with that list (java.lang.Object left out), and which
# declare no member type that a subclass inherits (a public or protected nested type: `Map.Entry`, `Thread.State`,
# `Enum.EnumDesc`). Read off JDK 21 with `javap -v -p` (the InnerClasses entries of each, and the supers of its
# header, closed over the JDK's own types). A repo type with any other external supertype may inherit a member type of
# that supertype, which hides a type of the same name from an outer scope or an import, and may itself be an instance
# of any type that supertype extends: neither is known, so a name or a call that depends on it is not proven.
KNOWN_SUPERTYPES: dict[str, tuple[str, ...]] = {
    "java.io.Serializable": (), "java.lang.Cloneable": (), "java.lang.Comparable": (), "java.lang.Runnable": (),
    "java.util.concurrent.Callable": (), "java.lang.AutoCloseable": (), "java.lang.Iterable": (), "java.util.Iterator": (),
    "java.lang.CharSequence": (), "java.lang.Appendable": (), "java.util.RandomAccess": (), "java.util.Comparator": (),
    "java.util.function.Supplier": (), "java.util.function.Function": (), "java.util.function.Consumer": (),
    "java.util.function.Predicate": (), "java.util.function.BiFunction": (), "java.util.function.BiConsumer": (),
    "java.io.Flushable": (), "java.util.EventListener": (), "java.lang.annotation.Annotation": (), "java.lang.Record": (),
    "java.util.Map.Entry": (), "java.lang.reflect.Type": (), "java.lang.reflect.InvocationHandler": (),
    "java.util.concurrent.ThreadFactory": (),
    "java.io.Closeable": ("java.lang.AutoCloseable",),
    "java.lang.reflect.ParameterizedType": ("java.lang.reflect.Type",),
    "java.lang.reflect.GenericArrayType": ("java.lang.reflect.Type",),
    "java.lang.reflect.WildcardType": ("java.lang.reflect.Type",),
    "java.lang.reflect.TypeVariable": ("java.lang.reflect.AnnotatedElement", "java.lang.reflect.Type"),
    "java.util.ListIterator": ("java.util.Iterator",),
    "java.util.Random": ("java.io.Serializable", "java.util.random.RandomGenerator"),
    "java.beans.VetoableChangeListener": ("java.util.EventListener",),
    "java.util.function.UnaryOperator": ("java.util.function.Function",),
    "java.util.function.BinaryOperator": ("java.util.function.BiFunction",),
    "java.lang.Number": ("java.io.Serializable",),
    "java.lang.Throwable": ("java.io.Serializable",),
    "java.lang.Enum": ("java.io.Serializable", "java.lang.Comparable", "java.lang.constant.Constable"),
    "java.lang.Exception": ("java.io.Serializable", "java.lang.Throwable"),
    "java.lang.Error": ("java.io.Serializable", "java.lang.Throwable"),
    "java.io.IOException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.InterruptedException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.CloneNotSupportedException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.ReflectiveOperationException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.AssertionError": ("java.io.Serializable", "java.lang.Error", "java.lang.Throwable"),
    "java.io.FileNotFoundException": ("java.io.IOException", "java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.RuntimeException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    **{
        name: ("java.io.Serializable", "java.lang.Exception", "java.lang.RuntimeException", "java.lang.Throwable")
        for name in (
            "java.lang.IllegalArgumentException", "java.lang.IllegalStateException", "java.lang.UnsupportedOperationException",
            "java.lang.ClassCastException", "java.lang.NullPointerException", "java.lang.IndexOutOfBoundsException",
            "java.lang.SecurityException", "java.lang.ArithmeticException", "java.util.NoSuchElementException",
            "java.util.ConcurrentModificationException", "java.io.UncheckedIOException",
        )
    },
    "java.lang.NumberFormatException": (
        "java.io.Serializable", "java.lang.Exception", "java.lang.IllegalArgumentException", "java.lang.RuntimeException",
        "java.lang.Throwable",
    ),
}  # fmt: skip
# the exceptions to "declares none": java.util.Random inherits the member interfaces of RandomGenerator (JDK 17 and up)
MEMBER_TYPES: dict[str, frozenset[str]] = {
    "java.lang.Enum": frozenset({"EnumDesc"}),
    "java.util.Random": frozenset({"StreamableGenerator", "SplittableGenerator", "JumpableGenerator", "LeapableGenerator", "ArbitrarilyJumpableGenerator"}),
}  # fmt: skip

# The public and protected methods and fields (static ones too) that each type of KNOWN_SUPERTYPES, and each type it
# names above it, declares itself: `javap -protected` on JDK 21, nothing from a later JDK is in it. KNOWN_MEMBERS is the
# union over a type and its supertypes.
DECLARED_MEMBERS: dict[str, dict[str, tuple[int, ...]]] = {
    "java.beans.VetoableChangeListener": {"vetoableChange": (1,)},
    "java.io.Closeable": {"close": (0,)},
    "java.io.FileNotFoundException": {},
    "java.io.Flushable": {"flush": (0,)},
    "java.io.IOException": {},
    "java.io.Serializable": {},
    "java.io.UncheckedIOException": {"getCause": (0,)},
    "java.lang.Appendable": {"append": (1, 3)},
    "java.lang.ArithmeticException": {},
    "java.lang.AssertionError": {},
    "java.lang.AutoCloseable": {"close": (0,)},
    "java.lang.CharSequence": {
        "charAt": (1,), "chars": (0,), "codePoints": (0,), "isEmpty": (0,), "length": (0,), "subSequence": (2,),
        "toString": (0,)
    },
    "java.lang.ClassCastException": {},
    "java.lang.CloneNotSupportedException": {},
    "java.lang.Cloneable": {},
    "java.lang.Comparable": {"compareTo": (1,)},
    "java.lang.Enum": {
        "clone": (0,), "compareTo": (1,), "describeConstable": (0,), "equals": (1,), "finalize": (0,),
        "getDeclaringClass": (0,), "hashCode": (0,), "name": (0,), "ordinal": (0,), "toString": (0,), "valueOf": (2,)
    },
    "java.lang.Error": {},
    "java.lang.Exception": {},
    "java.lang.IllegalArgumentException": {},
    "java.lang.IllegalStateException": {},
    "java.lang.IndexOutOfBoundsException": {},
    "java.lang.InterruptedException": {},
    "java.lang.Iterable": {"forEach": (1,), "iterator": (0,), "spliterator": (0,)},
    "java.lang.NullPointerException": {"fillInStackTrace": (0,), "getMessage": (0,)},
    "java.lang.Number": {
        "byteValue": (0,), "doubleValue": (0,), "floatValue": (0,), "intValue": (0,), "longValue": (0,), "shortValue":
        (0,)
    },
    "java.lang.NumberFormatException": {},
    "java.lang.ReflectiveOperationException": {},
    "java.lang.Runnable": {"run": (0,)},
    "java.lang.RuntimeException": {},
    "java.lang.SecurityException": {},
    "java.lang.Throwable": {
        "addSuppressed": (1,), "fillInStackTrace": (0,), "getCause": (0,), "getLocalizedMessage": (0,), "getMessage":
        (0,), "getStackTrace": (0,), "getSuppressed": (0,), "initCause": (1,), "printStackTrace": (0, 1),
        "setStackTrace": (1,), "toString": (0,)
    },
    "java.lang.UnsupportedOperationException": {},
    "java.lang.annotation.Annotation": {"annotationType": (0,), "equals": (1,), "hashCode": (0,), "toString": (0,)},
    "java.lang.constant.Constable": {"describeConstable": (0,)},
    "java.lang.reflect.AnnotatedElement": {
        "getAnnotation": (1,), "getAnnotations": (0,), "getAnnotationsByType": (1,), "getDeclaredAnnotation": (1,),
        "getDeclaredAnnotations": (0,), "getDeclaredAnnotationsByType": (1,), "isAnnotationPresent": (1,)
    },
    "java.lang.reflect.GenericArrayType": {"getGenericComponentType": (0,)},
    "java.lang.reflect.InvocationHandler": {"invoke": (3,)},
    "java.lang.reflect.ParameterizedType": {"getActualTypeArguments": (0,), "getOwnerType": (0,), "getRawType": (0,)},
    "java.lang.reflect.Type": {"getTypeName": (0,)},
    "java.lang.reflect.TypeVariable": {
        "getAnnotatedBounds": (0,), "getBounds": (0,), "getGenericDeclaration": (0,), "getName": (0,)
    },
    "java.lang.reflect.WildcardType": {"getLowerBounds": (0,), "getUpperBounds": (0,)},
    "java.util.Comparator": {
        "compare": (2,), "equals": (1,), "reversed": (0,), "thenComparing": (1, 2), "thenComparingDouble": (1,),
        "thenComparingInt": (1,), "thenComparingLong": (1,)
    },
    "java.util.ConcurrentModificationException": {},
    "java.util.EventListener": {},
    "java.util.Iterator": {"forEachRemaining": (1,), "hasNext": (0,), "next": (0,), "remove": (0,)},
    "java.util.ListIterator": {
        "add": (1,), "hasNext": (0,), "hasPrevious": (0,), "next": (0,), "nextIndex": (0,), "previous": (0,),
        "previousIndex": (0,), "remove": (0,), "set": (1,)
    },
    "java.util.Map.Entry": {"equals": (1,), "getKey": (0,), "getValue": (0,), "hashCode": (0,), "setValue": (1,)},
    "java.util.NoSuchElementException": {},
    "java.util.Random": {
        "doubles": (0, 1, 2, 3), "from": (1,), "ints": (0, 1, 2, 3), "longs": (0, 1, 2, 3), "next": (1,), "nextBoolean":
        (0,), "nextBytes": (1,), "nextDouble": (0,), "nextFloat": (0,), "nextGaussian": (0,), "nextInt": (0, 1),
        "nextLong": (0,), "setSeed": (1,)
    },
    "java.util.RandomAccess": {},
    "java.util.concurrent.Callable": {"call": (0,)},
    "java.util.concurrent.ThreadFactory": {"newThread": (1,)},
    "java.util.function.BiConsumer": {"accept": (2,), "andThen": (1,)},
    "java.util.function.BiFunction": {"andThen": (1,), "apply": (2,)},
    "java.util.function.BinaryOperator": {},
    "java.util.function.Consumer": {"accept": (1,), "andThen": (1,)},
    "java.util.function.Function": {"andThen": (1,), "apply": (1,), "compose": (1,)},
    "java.util.function.Predicate": {"and": (1,), "negate": (0,), "or": (1,), "test": (1,)},
    "java.util.function.Supplier": {"get": (0,)},
    "java.util.function.UnaryOperator": {},
    "java.util.random.RandomGenerator": {
        "doubles": (0, 1, 2, 3), "ints": (0, 1, 2, 3), "isDeprecated": (0,), "longs": (0, 1, 2, 3), "nextBoolean": (0,),
        "nextBytes": (1,), "nextDouble": (0, 1, 2), "nextExponential": (0,), "nextFloat": (0, 1, 2), "nextGaussian":
        (0, 2), "nextInt": (0, 1, 2), "nextLong": (0, 1, 2)
    },
}  # fmt: skip


def _known_members(name: str) -> dict[str, tuple[int, ...]]:
    """What a type of KNOWN_SUPERTYPES and everything above it declare: member -> the numbers of parameters."""
    found: dict[str, set[int]] = {}
    for above in (name, *KNOWN_SUPERTYPES[name]):
        for member, counts in DECLARED_MEMBERS.get(above, {}).items():
            found.setdefault(member, set()).update(counts)
    return {member: tuple(sorted(counts)) for member, counts in found.items()}


KNOWN_MEMBERS.update({name: _known_members(name) for name in KNOWN_SUPERTYPES if name != "java.lang.Record"})
KNOWN_MEMBERS["java.lang.Enum"].update({member: ANY for member in ENUM_MEMBERS})
# JDK classes that are final, so no repo type is an instance of one (`javap` prints `final class`): a call on a value
# of such a type cannot run repo code.
FINAL_EXTERNAL = frozenset(
    {
        "java.lang." + n
        for n in ("String", "StringBuilder", "StringBuffer", "Integer", "Long", "Short", "Byte", "Character", "Boolean", "Double",
                  "Float", "Class", "Math", "StrictMath", "System", "Void", "StackTraceElement", "ProcessBuilder")
    }
    | {
        "java.util." + n
        for n in ("Objects", "Optional", "OptionalInt", "OptionalLong", "OptionalDouble", "Arrays", "UUID", "Scanner", "StringJoiner", "Locale")
    }
    | {"java.util.regex.Pattern", "java.util.regex.Matcher"}
)  # fmt: skip

TYPE_ASKS = ("type_identifier", "scoped_type_identifier", "generic_type", "annotation", "marker_annotation", "type_arguments")
STATEMENT_ENDS = ("statement", "declaration", "declarator")
OBJECT_EXTERNAL = ("ext", OBJECT)

IMPLICIT_RECORD_ACCESSOR = "implicit record accessor"
IMPLICIT_ENUM_MEMBER = "implicit enum member"
OBJECT_MEMBER = "java.lang.Object member"
NO_REPO_METHOD = "no repo method has this name and arity"


def arguments_of(node) -> list:
    """The argument expressions of a call, `new`, `this(...)`, `super(...)` or enum constant: a comment between them
    (or around them) is no argument."""
    argl = node.child_by_field_name("arguments")
    return named(argl) if argl is not None else []


def _collapse(source: str, limit: int = 80) -> str:
    source = " ".join(source.split())
    return source if len(source) <= limit else source[: limit - 3] + "..."


class JavaResolver:
    """Resolves every call site of the declared types (see the module comment). run() fills edges, unresolved and
    external (why a call counts as outside the repo -> how many)."""

    def __init__(self, declarations: Declarations, profile: LanguageProfile):
        self.d = declarations
        self.java_lang = profile.builtin_names
        self.jdk_roots = profile.stdlib_module_names
        self.edges: list[Edge] = []
        self.unresolved: list[Unresolved] = []
        self.external: Counter = Counter()
        self._supers: dict[JavaType, list] = {}
        self._ancestors: dict[JavaType, tuple[list[JavaType], list]] = {}
        self._declares: dict[tuple[JavaType, str], bool] = {}
        self._types: dict[tuple, tuple] = {}
        self._written: dict[tuple[int, str], list[int]] = {}
        self._signatures: dict[JavaMethod, tuple[str, ...]] = {}
        self._invocations: dict[int, tuple] = {}
        self._blocks: dict[int, list[tuple]] = {}
        self._patterns: dict[int, dict[str, set[str]]] = {}
        self._subs: dict[JavaType, list[JavaType]] = {}
        self._items: dict[tuple, tuple[str, ...]] = {}
        self._implicit: dict[JavaType, list[JavaMethod]] = {}
        self._views: dict[tuple, dict[str, str] | None] = {}
        self._ext_asked: dict[tuple[str, str, int], bool] = {}
        self.ready = False
        self.by_name: dict[str, list[JavaMethod]] = defaultdict(list)
        for t in self.d.every_type:
            for found in t.methods.values():
                for m in found:
                    self.by_name[m.name].append(m)
        self.lombok = any(LOMBOK & t.annotations for t in self.d.every_type)
        # methods whose header the parser could not read (JavaMethod.broken): a call of their name is not decided
        self.broken_names = {m.name for t in self.d.every_type for m in [*(x for ms in t.methods.values() for x in ms), *t.ctors] if m.broken}

    # ---- supertypes --------------------------------------------------------------------------------------------

    def supertypes(self, t: JavaType) -> list:
        """The supertypes of a type: a JavaType for each that is in the repo, ("ext", name) for one from outside
        it, and ("unres", written) for one whose name does not resolve."""
        return self.supers_of(t)

    def supers_of(self, t: JavaType) -> list:
        known = self._supers.get(t)
        if known is not None:
            return known
        found: list = []
        self._supers[t] = found  # a type that names itself through a cycle sees what is resolved so far
        if t.fixed_supers is not None:
            found.extend(t.fixed_supers)
        else:
            for written, _ in t.supers:
                # the extends and implements clauses are outside the body: t's own member types are not in scope there
                resolved = self.resolve_type(written, t, t.ctx_method if isinstance(t.ctx_method, JavaMethod) else None, header=True)
                if resolved[0] == "repo":
                    found.append(resolved[1])
                elif resolved[0] == "ext":
                    found.append(("ext", resolved[1]))
                else:
                    found.append(("unres", written))
            if t.kind == "enum":
                found.append(("ext", "java.lang.Enum"))
            if t.kind == "record":
                found.append(("ext", "java.lang.Record"))
        return found

    def ancestors(self, t: JavaType) -> tuple[list[JavaType], list]:
        """(t and every supertype in the repo, breadth first, a class before its interfaces; the supertypes from
        outside the repo, and the unresolved ones, met on the way)."""
        known = self._ancestors.get(t)
        if known is not None:
            return known
        seen, order, external = {t}, [t], []
        i = 0
        while i < len(order):
            current = order[i]
            i += 1
            for sup in self.supers_of(current):
                if isinstance(sup, JavaType):
                    if sup not in seen:
                        seen.add(sup)
                        order.append(sup)
                else:
                    external.append(sup)
        result = (order, external)
        if self.ready:
            self._ancestors[t] = result
        return result

    def all_subs(self, t: JavaType) -> list[JavaType]:
        known = self._subs.get(t)
        if known is None:
            seen, todo, known = {t}, list(t.subs), []
            while todo:
                x = todo.pop()
                if x in seen:
                    continue
                seen.add(x)
                known.append(x)
                todo += x.subs
            self._subs[t] = known
        return known

    # ---- type names --------------------------------------------------------------------------------------------

    def _is_type_variable(self, name: str, ctx: JavaType | None, m) -> bool:
        member = m
        while isinstance(member, JavaMethod):
            if name in member.type_parameters:
                return True
            member = member.owner.ctx_method
        c = ctx
        while c is not None:
            if name in c.type_parameters:
                return True
            if isinstance(c.ctx_method, JavaMethod) and name in c.ctx_method.type_parameters:
                return True
            c = c.outer
        return False

    def _member_type(self, name: str, c: JavaType):
        """The member type `name` that c declares or inherits from a supertype in the repo (a private one of a
        supertype is not inherited, nor a package-private one in another package), else None."""
        for a in self.ancestors(c)[0]:
            member = a.members.get(name)
            if member is None:
                continue
            if a is c or (
                "private" not in member.mods
                and (a.kind == "interface" or member.mods & {"public", "protected"} or self._package_inherits(c, a))
            ):
                return member
        return None

    def _member_type_blocker(self, c: JavaType, name: str) -> str:
        """Why `name` may be a member type that c inherits from a supertype outside the repo (see KNOWN_SUPERTYPES), or ""."""
        for e in self.ancestors(c)[1]:
            if e != OBJECT_EXTERNAL and not (e[0] == "ext" and e[1] in KNOWN_SUPERTYPES and name not in MEMBER_TYPES.get(e[1], ())):
                return f"type {name} may be a member type that {c.fqn} inherits from {e[1]}, which ripple cannot see into"
        return ""

    def _local_type(self, local: JavaType, name: str) -> tuple:
        """A local class is in scope from its declaration to the end of its block (JLS 6.3), and the place a name is
        used at is not known here. So the local class is the answer only when the name is written nowhere else in the
        member that holds it: written before the declaration or after the block, it means something else."""
        holder = local.ctx_method.node if isinstance(local.ctx_method, JavaMethod) else local.outer.node
        key = (holder.id, name)
        spots = self._written.get(key)
        if spots is None:
            spots, stack = [], [holder]
            while stack:
                x = stack.pop()
                if x.type in ("identifier", "type_identifier") and text(x) == name:
                    spots.append(x.start_byte)
                stack += x.named_children
            self._written[key] = spots
        low, high = local.node.start_byte, local.node.parent.end_byte
        if any(spot < low or spot >= high for spot in spots):
            return ("unres", f"{name} is a local class, and the name is also written outside its scope in the same member")
        return ("repo", local)

    def find_simple_type(self, name: str, ctx: JavaType, m, header: bool = False) -> tuple:
        if self._is_type_variable(name, ctx, m):
            return ("tvar", name)
        if isinstance(m, JavaMethod):  # a local class of the member itself comes first (JLS 6.4.1: it hides the members)
            local = self.d.types.get(f"{m.id}.{name}")
            if local is not None:
                return self._local_type(local, name)
        c = ctx  # (the member a local class sits in is looked at below, after the class's own and inherited member types)
        while c is not None:
            if not (header and c is ctx):  # (a class's own members are not in scope in its own header)
                if c.simple == name and c.kind != "anon":
                    return ("repo", c)
                found = self._member_type(name, c)
                if found is not None:
                    return ("repo", found)
                blocked = self._member_type_blocker(c, name)
                if blocked:
                    return ("unres", blocked)
            if isinstance(c.ctx_method, JavaMethod):
                local = self.d.types.get(f"{c.ctx_method.id}.{name}")
            else:  # a local class of an initializer block is named for the class around it
                local = self.d.types.get(f"{c.fqn}.{name}")
                local = local if local is not None and local.is_local and local.outer is c else None
            if local is not None:
                return self._local_type(local, name)
            c = c.outer
        fi = ctx.finfo
        if name in fi.tops:
            return ("repo", fi.tops[name])
        if name in fi.single:
            qualified = fi.single[name]
            found = self.repo_type_by_qualified(qualified)
            return ("repo", found) if found else ("ext", qualified)
        same = self.d.by_pkg.get(fi.pkg, {})
        if name in same:
            return ("repo", same[name])
        found, external_on_demand = [], False
        for star in fi.ondemand:
            if star in self.d.by_pkg and name in self.d.by_pkg[star]:
                found.append(self.d.by_pkg[star][name])
                continue
            owner = self.repo_type_by_qualified(star)
            if owner is None:
                external_on_demand = True
            elif name in owner.members:
                found.append(owner.members[name])
        if len(found) == 1:
            return ("repo", found[0])
        if len(found) > 1:
            return ("unres", f"two on-demand imports provide {name}")
        if name in self.java_lang:
            return ("ext", "java.lang." + name)
        if external_on_demand:
            return ("ext", "?." + name)  # a type from a package an on-demand import names: which one is not known
        return ("unres", f"type {name} is not visible")

    def repo_type_by_qualified(self, qualified: str) -> JavaType | None:
        if qualified in self.d.types:
            return self.d.types[qualified]
        parts = qualified.split(".")
        for i in range(len(parts) - 1, 0, -1):
            head = self.d.types.get(".".join(parts[:i]))
            if head is not None:
                current = head
                for part in parts[i:]:
                    current = current.members.get(part)
                    if current is None:
                        return None
                return current
        return None

    def resolve_type(self, written: str, ctx: JavaType, m=None, header: bool = False) -> tuple:
        """What a type written in `ctx` (inside member m) is: ("repo", JavaType), ("ext", qualified name),
        ("prim", name), ("array", element, dimensions), ("tvar", name), ("var", ""), or ("unres", why)."""
        key = (written, ctx, m, header)
        known = self._types.get(key)
        if known is None:
            known = self._types[key] = self._resolve_type(written, ctx, m, header)
        return known

    def _resolve_type(self, written: str, ctx: JavaType, m, header: bool) -> tuple:
        base, dims = strip_type(written)
        if dims:
            return ("array", self.resolve_type(base, ctx, m, header), dims)
        if base in PRIMITIVES:
            return ("prim", base)
        if base == "var" or not base:
            return ("var", "")
        parts = base.split(".")
        first = self.find_simple_type(parts[0], ctx, m, header)
        if first[0] in ("ext", "unres") and len(parts) > 1 and parts[0][:1].islower():
            # `b.Twin`: a lower-case first name that no scope supplies as a type is a package, whatever an on-demand
            # import of a package outside the repo might hold. (Java's own naming rule; no type is called `b`.)
            qualified = self.repo_type_by_qualified(base)
            if qualified is not None:
                return ("repo", qualified)
        if first[0] == "repo":
            current = first[1]
            for part in parts[1:]:
                found = self._member_type(part, current)
                if found is None:
                    return ("unres", f"no member type {part} in {current.fqn}")
                current = found
            return ("repo", current)
        if first[0] == "ext" and len(parts) > 1:
            return ("ext", first[1] + "." + ".".join(parts[1:]))
        if first[0] in ("ext", "tvar"):
            return first
        if len(parts) > 1:  # package-qualified
            qualified = self.repo_type_by_qualified(base)
            if qualified is not None:
                return ("repo", qualified)
            if parts[0][:1].islower():
                return ("ext", base)
        return first

    def from_written(self, written: str, ctx: JavaType, m=None) -> tuple:
        """The static type of a value declared with a written type: ("repo", T, exact) | ("ext", name) |
        ("prim", name) | ("array",) | ("unknown", why). Never "exact": a declared type may be a subtype's value."""
        r = self.resolve_type(written, ctx, m)
        kind = r[0]
        if kind == "repo":
            return ("unknown", f"two files define {r[1].fqn}") if r[1].dup else ("repo", r[1], False)
        if kind == "tvar":
            return ("unknown", "the receiver is typed with a type variable")
        if kind == "var":
            return ("unknown", "var")
        if kind == "unres":
            return ("unknown", "the receiver's type name is not visible: " + r[1])
        if kind == "array":
            return ("array", "")
        return r

    # ---- variables ---------------------------------------------------------------------------------------------

    def lookup_var(self, name: str, at):
        """The declaration of a local, parameter or lambda parameter visible at the node `at`:
        ("local", written type, initializer) | ("lambda", "", None) | ("unknown", why, None) | None."""
        node, prev = at.parent, at
        while node is not None:
            kind = node.type
            if kind in ("block", "switch_block_statement_group", "constructor_body", "program"):
                for start, declared, written, value in self._block_declarations(node):
                    if start >= prev.start_byte:
                        break
                    if declared == name:
                        return None if written is None else ("local", written, value)  # None: a local class of that name
            elif kind == "switch_block":  # a local of a case group stays in scope for the groups after it
                for group in node.named_children:
                    if group.start_byte >= prev.start_byte:
                        break
                    for _, declared, written, value in self._block_declarations(group) if group.type == "switch_block_statement_group" else ():
                        if declared == name:
                            return None if written is None else ("local", written, value)
            elif kind in (*CALLABLES, "lambda_expression"):
                found = self._parameter(node, name)
                if found is not None:
                    return found
                if kind != "lambda_expression":
                    break
            elif kind == "for_statement":
                init = node.child_by_field_name("init")
                if init is not None and init.type == "local_variable_declaration":
                    for d in init.named_children:
                        if d.type == "variable_declarator" and text(d.child_by_field_name("name")) == name:
                            return ("local", text(init.child_by_field_name("type")), d.child_by_field_name("value"))
            elif kind == "enhanced_for_statement":
                if text(node.child_by_field_name("name")) == name:
                    written = text(node.child_by_field_name("type"))
                    return ("local", written, None) if written != "var" else ("unknown", "the receiver is a `var` loop variable", None)
            elif kind == "catch_clause":
                parameter = next((c for c in node.named_children if c.type == "catch_formal_parameter"), None)
                if parameter is not None and text(parameter.child_by_field_name("name")) == name:
                    types = next((c for c in parameter.named_children if c.type == "catch_type"), None)
                    if types is not None and len(named(types)) == 1:
                        return ("local", text(types), None)
                    return ("unknown", "the receiver is a multi-catch parameter", None)
            elif kind == "try_with_resources_statement":
                resources = node.child_by_field_name("resources")
                in_scope = prev == node.child_by_field_name("body") or prev == resources  # not in the catch or finally
                for r in resources.named_children if resources and in_scope else ():
                    if r.type == "resource" and text(r.child_by_field_name("name")) == name:
                        return ("local", text(r.child_by_field_name("type")), r.child_by_field_name("value"))
            elif kind in TYPE_DECLS or kind == "class_body" or (kind == "object_creation_expression" and prev.type == "class_body"):
                break
            prev, node = node, node.parent
        return self._pattern_variable(name, at)

    def _block_declarations(self, block) -> list[tuple]:
        """What a block declares directly, in source order: (start byte, name, written type, initializer) for a local
        variable, (start byte, name, None, None) for a local class. Read once per block."""
        known = self._blocks.get(block.id)
        if known is None:
            known = self._blocks[block.id] = []
            for c in block.named_children:
                if c.type == "local_variable_declaration":
                    written = text(c.child_by_field_name("type"))
                    for d in c.named_children:
                        if d.type == "variable_declarator":
                            known.append((c.start_byte, text(d.child_by_field_name("name")), written, d.child_by_field_name("value")))
                elif c.type in TYPE_DECLS:
                    known.append((c.start_byte, text(c.child_by_field_name("name")), None, None))
        return known

    @staticmethod
    def _parameter(node, name: str):
        parameters = node.child_by_field_name("parameters")
        if node.type == "lambda_expression":
            if parameters is None:  # `x -> ...`
                first = next((c for c in node.named_children if c.type == "identifier"), None)
                return ("lambda", "", None) if first is not None and text(first) == name else None
            if parameters.type == "identifier":
                return ("lambda", "", None) if text(parameters) == name else None
            for p in parameters.named_children:
                if p.type == "identifier" and text(p) == name:
                    return ("lambda", "", None)
                if p.type == "formal_parameter" and text(p.child_by_field_name("name")) == name:
                    return ("local", text(p.child_by_field_name("type")), None)
                if p.type == "inferred_parameters" and any(text(x) == name for x in p.named_children):
                    return ("lambda", "", None)
            return None
        for p in parameters.named_children if parameters is not None else ():
            if p.type == "formal_parameter" and text(p.child_by_field_name("name")) == name:
                return ("local", text(p.child_by_field_name("type")), None)
            if p.type == "spread_parameter":
                d = next((c for c in p.named_children if c.type == "variable_declarator"), None)
                if d is not None and text(d.child_by_field_name("name")) == name:
                    return ("local", "x[]", None)
        return None

    def _pattern_variable(self, name: str, at):
        """A pattern binding (`x instanceof Item i`, `case Item i ->`, a component of a record pattern, nested ones
        too) is in scope by flow, not by block: any in the enclosing callable counts when they all give the name one
        type. A lambda is no boundary (it sees the bindings around it); the body of a local or anonymous class is."""
        holder, below = at, None
        while holder is not None and holder.type not in CALLABLES:
            if holder.type == "class_body":  # a field initializer or an initializer block: that member is the holder
                holder = below
                break
            below, holder = holder, holder.parent
        if holder is None:
            return None
        bindings = self._patterns.get(holder.id)
        if bindings is None:  # one walk of the callable gives every name, not one walk for each lookup
            bindings = self._patterns[holder.id] = defaultdict(set)
            stack = [holder]
            while stack:
                x = stack.pop()
                if x.type in ("type_pattern", "record_pattern_component"):
                    parts = [c for c in named(x) if c.type != "modifiers"]
                    if len(parts) >= 2:
                        bindings[text(parts[-1])].add(text(parts[0]))
                elif x.type == "instanceof_expression":
                    bound = x.child_by_field_name("name")
                    if bound is not None:
                        bindings[text(bound)].add(text(x.child_by_field_name("right")))
                if x.type != "class_body":
                    stack += x.named_children  # (a walk)
        found = bindings.get(name)
        if not found:
            return None
        if len(found) == 1:  # "pattern": the caller checks that nothing else (a field) has the name, since a binding is only in scope by flow
            return ("pattern", next(iter(found)), None)
        return ("unknown", "the receiver is a pattern variable bound with different types", None)

    def _field_member(self, a: JavaType, name: str, rt: JavaType) -> bool:
        """Whether the field `name` that a declares is a member of rt: not a private field of a supertype, nor a
        package-private one in another package (JLS 8.2)."""
        if a is rt:
            return True
        mods = a.field_mods.get(name, set())
        if "private" in mods:
            return False
        return a.kind == "interface" or bool(mods & {"public", "protected"}) or self._package_inherits(rt, a)

    def field_of(self, t: JavaType, name: str):
        """("field", written type, the type that declares it) from t or its repo supertypes; ("maybe-ext",) when a
        supertype from outside the repo could hold a field of that name; None when nothing does."""
        order, external = self.ancestors(t)
        for a in order:
            if name in a.fields and self._field_member(a, name, t):
                return ("field", a.fields[name][0], a)
        if any(self.may_declare(e, name, field=True) for e in external if e != OBJECT_EXTERNAL):
            return ("maybe-ext",)
        return None

    # ---- what an expression is ---------------------------------------------------------------------------------

    def type_of(self, e, t: JavaType, m, depth: int = 0) -> tuple:
        """The static type of an expression: ("repo", JavaType, exact) | ("ext", name) | ("prim", name) | ("array",)
        | ("type", JavaType or name) for a type name | ("pkg", dotted) for the start of a qualified name |
        ("unknown", why)."""
        if depth > 12:
            return ("unknown", "too deep")
        kind = e.type
        if kind == "parenthesized_expression":
            return self.type_of(named(e)[0], t, m, depth + 1)
        if kind == "string_literal":
            return ("ext", "java.lang.String")
        if kind in ("decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal"):
            return ("prim", "long" if text(e).lower().endswith("l") else "int")
        if kind in ("decimal_floating_point_literal", "hex_floating_point_literal"):
            return ("prim", "float" if text(e).lower().endswith("f") else "double")
        if kind in ("true", "false"):
            return ("prim", "boolean")
        if kind == "character_literal":
            return ("prim", "char")
        if kind == "null_literal":
            return ("unknown", "null")
        if kind == "class_literal":
            return ("ext", "java.lang.Class")
        if kind == "this":
            return ("repo", t, False)
        if kind == "cast_expression":
            return self.from_written(text(e.child_by_field_name("type")), t, m)
        if kind == "object_creation_expression":
            r = self.from_written(text(e.child_by_field_name("type")), t, m)
            return ("repo", r[1], not any(c.type == "class_body" for c in e.named_children)) if r[0] == "repo" else r
        if kind == "array_creation_expression":
            return ("array", "")
        if kind == "identifier":
            return self._type_of_name(e, t, m, depth)
        if kind == "field_access":
            return self._type_of_field(e, t, m, depth)
        if kind == "method_invocation":
            result = self.resolve_invocation(e, t, m)
            if result[0] == "edge":
                callee = self.d.methods.get(result[1][0][0])
                if callee is None:
                    return ("unknown", "the receiver is a call result")
                if not callee.ret or callee.ret == "void":
                    return ("unknown", "void")
                return self.from_written(callee.ret, callee.owner, callee)
            if result[0] == "ext":
                return ("unknown", "the receiver is the result of a call into code outside the repo")
            return ("unknown", "the receiver is the result of a call ripple cannot resolve")
        if kind == "binary_expression" and text(e.child_by_field_name("operator")) == "+":
            sides = [self.type_of(e.child_by_field_name(k), t, m, depth + 1) for k in ("left", "right")]
            if any(side == ("ext", "java.lang.String") for side in sides):
                return ("ext", "java.lang.String")
            return ("unknown", "the receiver is an arithmetic expression")
        if kind == "array_access":
            return ("unknown", "the receiver is an array element")
        if kind == "ternary_expression":
            return ("unknown", "the receiver is a conditional expression")
        if kind in ("lambda_expression", "method_reference"):
            return ("unknown", "function value")
        return ("unknown", f"the receiver is a {kind}")

    def _type_of_name(self, e, t: JavaType, m, depth: int) -> tuple:
        name = text(e)
        v = self.lookup_var(name, e)
        if v is not None:
            if v[0] == "lambda":
                return ("unknown", "the receiver is an untyped lambda parameter")
            if v[0] == "unknown":
                return ("unknown", v[1])
            if v[0] == "pattern" and self._hides_field(name, t):  # a binding is in scope by flow, which is not modelled
                return ("unknown", f"'{name}' is a pattern variable and also a field: which one a use means depends on flow")
            if strip_type(v[1])[0] == "var":
                if v[2] is not None:
                    r = self.type_of(v[2], t, m, depth + 1)
                    return r if r[0] != "type" else ("unknown", "var from a type name")
                return ("unknown", "the receiver is a `var` with no initializer ripple reads")
            return self.from_written(v[1], t, m)
        c = t
        while c is not None:
            f = self.field_of(c, name)
            if f is not None:
                if f[0] == "maybe-ext":
                    return ("unknown", f"'{name}' may be a field inherited from an external base of {c.fqn}")
                return self.from_written(f[1], f[2], None)
            if c.ctx_method is not None:  # a local or anonymous class sees the locals of the member it is in
                v = self.lookup_var(name, c.node)
                if v is not None:
                    if v[0] in ("lambda", "unknown", "pattern") or strip_type(v[1])[0] == "var":
                        return ("unknown", "captured local of unknown type")
                    return self.from_written(v[1], c.outer, c.ctx_method if isinstance(c.ctx_method, JavaMethod) else None)
            c = c.outer
        imported = self._imported_field(name, t)  # a variable obscures a type of the same name (JLS 6.4.2)
        if imported is not None:
            return imported
        r = self.resolve_type(name, t, m)
        if r[0] in ("repo", "ext"):
            return ("type", r[1])
        if name in t.finfo.static_single:
            return ("unknown", "the receiver is a static-imported field")
        return ("pkg", name)

    def _imported_field(self, name: str, t: JavaType):
        """The type of a field `name` that a static import of t's file may supply, ("unknown", why) when it may be one
        whose type is not known, or None when no static import can supply a field of that name. A static import of a
        type outside the repo may supply any name, so it leaves every name that is also a type unknown."""
        fi = t.finfo
        owners = [*fi.static_single.get(name, ()), *fi.static_ondemand]
        found = []
        for owner in dict.fromkeys(owners):
            rt = self.repo_type_by_qualified(owner)
            if rt is None:
                return ("unknown", f"'{name}' may be a field that the static import of {owner} supplies")
            f = self.field_of(rt, name)
            if f is not None and f[0] == "maybe-ext":
                return ("unknown", f"'{name}' may be a field that {owner} inherits from outside the repo")
            if f is not None and f[2].fields[name][1]:  # a static field
                found.append(f)
        if len(found) == 1 and len(dict.fromkeys(owners)) == 1:
            return self.from_written(found[0][1], found[0][2], None)
        if found:
            return ("unknown", f"'{name}' is a static-imported field of more than one type")
        return None

    def _hides_field(self, name: str, t: JavaType) -> bool:
        """Whether `name` is also a field (or a captured local) that t or a class around it can see."""
        c = t
        while c is not None:
            if self.field_of(c, name) is not None or (c.ctx_method is not None and self.lookup_var(name, c.node) is not None):
                return True
            c = c.outer
        return False

    def written_type_of(self, e, t: JavaType, m) -> str | None:
        """The type an expression is declared or written with (`List<String>`, generics kept), when it is a name, a cast
        or a `new`: what a parameter's type arguments are compared with. None when that is not read."""
        kind = e.type
        if kind == "parenthesized_expression":
            return self.written_type_of(named(e)[0], t, m)
        if kind in ("cast_expression", "object_creation_expression"):
            return text(e.child_by_field_name("type"))
        if kind != "identifier":
            return None
        name = text(e)
        v = self.lookup_var(name, e)
        if v is not None:
            return v[1] if v[0] in ("local", "pattern") and strip_type(v[1])[0] != "var" else None
        c = t
        while c is not None:
            f = self.field_of(c, name)
            if f is not None:
                return f[1] if f[0] == "field" else None
            c = c.outer
        return None

    def _type_of_field(self, e, t: JavaType, m, depth: int) -> tuple:
        obj, name = e.child_by_field_name("object"), text(e.child_by_field_name("field"))
        if obj.type == "super":
            return ("unknown", "super field")
        r = self.type_of(obj, t, m, depth + 1)
        if name == "this":  # `Outer.this`: the enclosing instance, of the type the name says
            return ("repo", r[1], False) if r[0] == "type" and isinstance(r[1], JavaType) else ("unknown", "the receiver is `X.this` of a type ripple does not read")
        if r[0] == "pkg":
            qualified = r[1] + "." + name
            found = self.repo_type_by_qualified(qualified)
            return ("type", found) if found is not None else ("pkg", qualified)
        if r[0] == "type":
            if isinstance(r[1], JavaType):
                f = self.field_of(r[1], name)  # a field is named first: T.f is an expression when T has a field f (JLS 6.5.2)
                if f and f[0] == "field":
                    return self.from_written(f[1], f[2], None)
                if f and f[0] == "maybe-ext":
                    return ("unknown", "static field maybe inherited from external base")
                found = self._member_type(name, r[1])
                if found is not None:
                    return ("type", found)
                return ("unknown", f"no field {name} on {r[1].fqn}")
            return ("unknown", "the receiver is a static field of an external class")
        if r[0] == "repo":
            f = self.field_of(r[1], name)
            if f and f[0] == "field":
                return self.from_written(f[1], f[2], None)
            return ("unknown", f"'{name}' is not a field ripple can type")
        if r[0] == "ext":
            return ("unknown", "the receiver is a field of an object from outside the repo")
        return r if r[0] == "unknown" else ("unknown", "field of a non-object")

    # ---- which method -------------------------------------------------------------------------------------------

    def signature(self, mm: JavaMethod) -> tuple[str, ...]:
        """The parameter types of a method as qualified names, "?" for what is not known (a type variable, a type
        from an on-demand import of a package the repo does not hold)."""
        known = self._signatures.get(mm)
        if known is None:
            known = self._signatures[mm] = tuple(self._signature_item(self.resolve_type(w, mm.owner, mm)) for w, _ in mm.params)
        return known

    def _signature_item(self, r: tuple) -> str:
        if r[0] == "repo":
            return r[1].fqn
        if r[0] == "prim":
            return r[1]
        if r[0] == "ext":
            return "?" if r[1].startswith("?.") else r[1]
        if r[0] == "array":
            element = self._signature_item(r[1])
            return "?" if element == "?" else element + "[]" * r[2]
        return "?"

    @staticmethod
    def _same_top(a: JavaType, b: JavaType) -> bool:
        while a.outer is not None:
            a = a.outer
        while b.outer is not None:
            b = b.outer
        return a is b

    def _package_inherits(self, rt: JavaType, a: JavaType) -> bool:
        """Whether rt inherits a package-private member of a (JLS 8.4.8): every class from rt up to a is in a's package,
        since each one inherits it from the one above and a class in another package does not."""
        if rt.pkg != a.pkg:
            return False
        seen, todo = {rt}, [rt]
        while todo:
            x = todo.pop()
            if x is a:
                return True
            for s in self.supers_of(x):
                if isinstance(s, JavaType) and s.pkg == a.pkg and s not in seen:
                    seen.add(s)
                    todo.append(s)
        return False

    def _member(self, mm: JavaMethod, a: JavaType, rt: JavaType) -> bool:
        """Whether a method declared in `a` is a member of `rt` (JLS 8.4.8): declared in it, or inherited, which a
        private method never is (not even inside one nest), nor a package-private one in another package, nor a static
        method of an interface."""
        if a is rt:
            return True
        if mm.private or (mm.static and a.kind == "interface"):
            return False
        return mm.access != "package" or self._package_inherits(rt, a)

    def _accessible(self, mm: JavaMethod, a: JavaType, rt: JavaType, t: JavaType, via_super: bool = False) -> bool:
        """Whether code in t may call a member of rt that a declares (JLS 6.6): a private one only inside its top-level
        class, a package-private one in its package, a protected one also from a subclass body, through a qualifier of
        that subclass's type or below (a static one through any)."""
        if mm.access == "public":
            return True
        if mm.access == "private":
            return self._same_top(a, t)
        if a.pkg == t.pkg:
            return True
        if mm.access == "protected":
            x = t
            while x is not None:
                if a in self.ancestors(x)[0] and (mm.static or via_super or rt is x or rt in self.all_subs(x)):
                    return True
                x = x.outer
        return False

    def declares(self, c: JavaType, name: str, static_only: bool = False) -> bool:
        """Whether c or a repo supertype of it has a member method named `name`, of any arity."""
        key = (c, name, static_only)
        known = self._declares.get(key)
        if known is None:
            known = self._declares[key] = any(
                self._member(mm, a, c) and (mm.static or not static_only) for a in self.ancestors(c)[0] for mm in a.methods.get(name, ())
            )
        return known

    def candidates(self, rt: JavaType, name: str, n: int, t: JavaType, via_super: bool = False, static_only: bool = False) -> tuple[list[JavaMethod], list]:
        """(the methods named `name` that take n arguments, that rt has and t may call, with those that another
        declaration overrides left out; the supertypes of rt from outside the repo)."""
        order, external = self.ancestors(rt)
        every = [
            (a, mm)
            for a in order
            for mm in a.methods.get(name, ())
            if mm.arity_ok(n) and (mm.static or not static_only) and self._member(mm, a, rt) and self._accessible(mm, a, rt, t, via_super)
        ]
        found: list[JavaMethod] = []
        for a, mm in every:
            sig = self.signature(mm)
            # A declaration in a subtype with the same parameter types overrides this one, and a concrete method of a
            # class does a default one of an interface. Types that are not known ("?": a type variable) say nothing
            # about whether they are the same, so neither is dropped.
            if "?" not in "".join(sig) and any(
                other is not mm
                and b is not a
                and self.signature(other) == sig
                and (a in self.ancestors(b)[0] or (a.kind == "interface" and b.kind != "interface" and not other.abstract))
                for b, other in every
            ):
                continue
            found.append(mm)
        return found, [e for e in external if e != OBJECT_EXTERNAL]

    def argument_types(self, args, t: JavaType, m) -> tuple[str, ...] | None:
        """The type of each argument as a qualified name, or None when any is not known exactly."""
        found = []
        for a in args:
            r = self.type_of(a, t, m)
            if r[0] == "repo":
                found.append(r[1].fqn)
            elif r[0] == "prim":
                found.append(r[1])
            elif r[0] == "ext" and not r[1].startswith("?."):
                found.append(r[1])
            else:
                return None
        return tuple(found)

    def _generics_agree(self, c: JavaMethod, args, t: JavaType, m) -> bool:
        """Types are compared erased, and a parameter written with type arguments (`List<String>`) takes an argument
        only when its type arguments are those (a `List<Integer>` is not applicable to it): the argument has to be a
        name, cast or `new` written with the same text, and the text may not name a type variable."""
        for (written, _), arg in zip(c.params, args):
            if "<" not in written:
                continue
            given = self.written_type_of(arg, t, m)
            if given is None or "".join(given.split()) != "".join(written.split()):
                return False
            if any(self._is_type_variable(word, c.owner, c) for word in re.findall(r"[A-Za-z_$][\w$]*", written)):
                return False
        return True

    def exact(self, found: list[JavaMethod], args, t: JavaType, m) -> JavaMethod | None:
        """The one candidate whose parameter types are exactly the arguments' types. An exact match is always the
        most specific method (JLS 15.12.2), so nothing but written, known types ever picks between overloads."""
        at = self.argument_types(args, t, m)
        if at is None:
            return None
        same = [c for c in found if self.signature(c) == at and not c.varargs and self._generics_agree(c, args, t, m)]
        return same[0] if len(same) == 1 else None

    @staticmethod
    def may_declare(ext: tuple, name: str, n: int | None = None, field: bool = False) -> bool:
        """Whether a supertype from outside the repo may declare a member `name` other than Object's (which a call checks
        on its own, by OBJECT_SIGNATURES) that takes n arguments. True unless the supertype is one whose members are known
        (KNOWN_MEMBERS) and lack it: nothing else about a type from outside the repo is known."""
        members = KNOWN_MEMBERS.get(ext[1]) if ext[0] == "ext" else None
        if members is None:
            return True
        counts = members.get(name)
        return not field and counts is not None and (n is None or ANY[0] in counts or n in counts)

    def label(self, qualified: str) -> str:
        if qualified.startswith("?."):
            return "(package unknown: on-demand import)"
        return "(JDK)" if qualified.split(".", 1)[0] in self.jdk_roots else "(library)"

    def select(self, rt: JavaType, name: str, args, t: JavaType, m, receiver_exact: bool, via_super: bool = False, static_only: bool = False) -> tuple:
        """The method a call of `name` on a receiver of type rt reaches: ("edge", [(id, kind)...]),
        ("ext", why) or ("unres", why). A receiver_exact one (`super`, a type name, `new T()`) cannot be a subtype's."""
        n = len(args)
        if rt.dup:
            return ("unres", f"two files define {rt.fqn}, so which one a build compiles is not provable")
        if rt.kind == "record" and n == 0 and any(component == name for _, component in rt.components) and not any(mm.arity_ok(0) for mm in rt.methods.get(name, ())):
            # the accessor the record writes itself implements whatever an interface of that name declares: no source runs
            return ("ext", IMPLICIT_RECORD_ACCESSOR)
        if name in self.broken_names and any(mm.broken for a in (*self.ancestors(rt)[0], *self.all_subs(rt)) for mm in a.methods.get(name, ())):
            return ("unres", f"a declaration of {name} that {rt.fqn} has or inherits could not be read: the parser met an error in its header")
        found, external = self.candidates(rt, name, n, t, via_super, static_only)
        order = self.ancestors(rt)[0]
        if not found:
            if any(name in a.methods for a in order) and not external:
                return ("unres", f"no method {name} of {rt.fqn} takes {n} arguments")
            # A method no repo class above rt declares is outside the repo, unless a subtype of rt declares it: the
            # receiver may be one, and a call of the inherited method runs the subtype's.
            if not receiver_exact and self.subs_declare(rt, name):
                return ("unres", f"'{name}' is not declared on {rt.fqn}, but a subtype of it declares {name}")
            if name in OBJECT_MEMBERS and not any(name in a.methods for a in order):
                return ("ext", OBJECT_MEMBER)
            if rt.kind == "enum" and name in ENUM_MEMBERS:
                return ("ext", IMPLICIT_ENUM_MEMBER)
            if rt.kind == "record" and n == 0 and any(component == name for _, component in rt.components):
                return ("ext", IMPLICIT_RECORD_ACCESSOR)
            if any(e[0] == "unres" for e in external):
                return ("unres", f"'{name}' is not declared on {rt.fqn}, and a supertype could not be resolved")
            if external:
                return ("ext", "inherited from an external supertype")
            if any(a.annotations & LOMBOK for a in order):
                return ("unres", f"'{name}' is not declared on {rt.fqn}, which Lombok annotates")
            return ("unres", f"'{name}' is not declared on {rt.fqn} or its supertypes in the repo (generated code?)")
        blockers = [e for e in external if self.may_declare(e, name, n)]
        if n == 0 and any(not c.params for c in found):
            # a method with no parameters that a type outside the repo declares too (`close()` of AutoCloseable) has the
            # signature of the candidate: the candidate overrides it, and there is no other
            blockers = [e for e in blockers if not (e[0] == "ext" and e[1] in KNOWN_MEMBERS and 0 in KNOWN_MEMBERS[e[1]].get(name, ()))]
        if blockers:  # the strict rule: a supertype from outside the repo may declare another `name`
            kind, written = blockers[0]
            if kind == "unres":
                return ("unres", f"a supertype of {rt.fqn} could not be resolved, and may declare another {name} taking {n} arguments")
            return ("unres", f"an external supertype ({written} {self.label(written)}) of {rt.fqn} may declare another {name} taking {n} arguments")
        object_signature = OBJECT_SIGNATURES.get((name, n))
        if len(found) == 1 and (object_signature is None or self.signature(found[0]) == object_signature):
            chosen = found[0]
        else:
            chosen = self.exact(found, args, t, m)
            if chosen is None:
                if object_signature is not None:
                    return ("unres", f"java.lang.Object also declares {name} taking {n} arguments, and the argument types are not known to be a repo method's")
                return ("unres", f"{len(found)} overloads of {name} on {rt.fqn} take {n} arguments")
        out = [(chosen.id, "call")]
        dispatches = not (
            receiver_exact
            or chosen.static
            or chosen.private
            or chosen.final
            or chosen.owner.kind in ("record", "enumconst", "anon")
            or "final" in chosen.owner.mods
            or (chosen.owner.kind == "enum" and not any(s.kind == "enumconst" for s in chosen.owner.subs))
        )
        if dispatches:
            for sub in self.all_subs(rt):
                declared = False
                for mm in sub.methods.get(name, ()):
                    if self._overrides(mm, chosen, sub):
                        out.append((mm.id, "override"))
                        declared = True
                if not declared:  # a subtype that inherits its implementation from a class that is not under rt
                    inherited = self._inherited_override(sub, chosen, rt)
                    if inherited is not None and (inherited.id, "override") not in out:
                        out.append((inherited.id, "override"))
        return ("edge", out)

    def subs_declare(self, rt: JavaType, name: str) -> bool:
        """Whether a repo subtype of rt declares an instance method named `name`."""
        return any(not mm.static and not mm.private for s in self.all_subs(rt) for mm in s.methods.get(name, ()))

    # ---- overrides ---------------------------------------------------------------------------------------------

    def _overrides(self, mm: JavaMethod, chosen: JavaMethod, s: JavaType) -> bool:
        """Whether mm, a method of s or one s inherits, overrides `chosen` for the instances of s (JLS 8.4.8.1): it takes
        the parameter types that chosen takes as s sees them. A type variable is compared as what s makes of it (a
        type argument of s's `implements Visitor<Item>`), never as "any type": `visit(String)` does not override
        `Visitor<Item>.visit(T)`, and what is not known (an unresolved or raw argument) overrides nothing."""
        if mm is chosen or mm.static or mm.private or len(mm.params) != len(chosen.params):
            return False
        if chosen.access == "package" and mm.owner.pkg != chosen.owner.pkg:
            return False  # a package-private method is not overridden from another package
        got, expected = self._param_items(mm, s), self._param_items(chosen, s)
        return all(g == e and not e.startswith("??") for g, e in zip(got, expected))

    def _inherited_override(self, sub: JavaType, chosen: JavaMethod, rt: JavaType) -> JavaMethod | None:
        """The method `sub` inherits from a superclass that is not under rt and that implements `chosen` for it: the call
        through rt runs it for an instance of sub (`class B extends A implements I {}` runs A.m for `I.m()`)."""
        under = {rt, *self.all_subs(rt)}
        c = sub
        while c is not None:
            if c is not sub:
                if c in under:
                    return None  # its own methods are edged as those of a subtype of rt
                for mm in c.methods.get(chosen.name, ()):
                    if self._member(mm, c, sub) and self._overrides(mm, chosen, sub):
                        return None if mm.abstract else mm
            above = [x for x in self.supers_of(c) if isinstance(x, JavaType) and x.kind != "interface"]
            c = above[0] if above else None
        return None

    def _param_items(self, mm: JavaMethod, s: JavaType) -> tuple[str, ...]:
        """The parameter types of mm as `s` sees them: the qualified erased name of a type, "?name" for one of s's type
        parameters, "?m" for one of the method's own, "??" for what is not known."""
        key = (mm, s)
        known = self._items.get(key)
        if known is None:
            env = self._viewed_from(s, mm.owner)
            items = []
            for written, _ in mm.params:
                base, dims = strip_type(written)
                if base in mm.type_parameters:
                    item = "?m"
                elif base in mm.owner.type_parameters:
                    item = env.get(base, "??") if env is not None else "??"
                elif self._is_type_variable(base, mm.owner, mm):
                    item = "??"
                else:
                    item = self._signature_item(self.resolve_type(base, mm.owner, mm))
                    item = "??" if item == "?" else item
                items.append(item if item.startswith("??") else item + "[]" * dims)
            known = self._items[key] = tuple(items)
        return known

    def _viewed_from(self, s: JavaType, a: JavaType) -> dict[str, str] | None:
        """What each type parameter of `a`, a supertype of s in the repo, stands for as s sees it (see _param_items), or
        None when a is not above s."""
        if a is s:
            return {p: "?" + p for p in s.type_parameter_order}
        key = (s, a)
        if key not in self._views:
            self._views[key] = self._view_walk(s, a, {p: "?" + p for p in s.type_parameter_order}, {s})
        return self._views[key]

    def _view_walk(self, c: JavaType, a: JavaType, env: dict[str, str], seen: set) -> dict[str, str] | None:
        if c is a:
            return env
        for (written, _), sup in zip(c.supers, self.supers_of(c)):
            if not isinstance(sup, JavaType) or sup in seen:
                continue
            args = self._type_arguments(written)
            inner = {p: self._argument_item(args[i], c, env) if args is not None and i < len(args) else "??" for i, p in enumerate(sup.type_parameter_order)}
            found = self._view_walk(sup, a, inner, seen | {sup})
            if found is not None:
                return found
        return None

    @staticmethod
    def _type_arguments(written: str) -> list[str] | None:
        """The type arguments written in `Visitor<Item, List<X>>`: ["Item", "List<X>"]; [] when none are written (a raw
        type); None for `Outer<A>.Inner`, whose arguments are not read."""
        start = written.find("<")
        if start < 0:
            return []
        depth, args, current = 0, [], []
        for i in range(start, len(written)):
            ch = written[i]
            if ch == "<":
                depth += 1
                if depth == 1:
                    continue
            elif ch == ">":
                depth -= 1
                if depth == 0:
                    if written[i + 1 :].lstrip().startswith("."):
                        return None
                    break
            elif ch == "," and depth == 1:
                args.append("".join(current).strip())
                current = []
                continue
            current.append(ch)
        args.append("".join(current).strip())
        return args

    def _argument_item(self, argument: str, c: JavaType, env: dict[str, str]) -> str:
        """What a type argument written in c's supertype clause is, with c's own type parameters replaced by env's."""
        if argument.startswith("?"):
            return "??"
        base, dims = strip_type(argument)
        if base in c.type_parameters:
            item = env.get(base, "??")
        elif self._is_type_variable(base, c, None):
            return "??"
        else:
            item = self._signature_item(self.resolve_type(base, c, None, header=True))
            item = "??" if item == "?" else item
        return item if item.startswith("??") else item + "[]" * dims

    # ---- call sites ---------------------------------------------------------------------------------------------

    def resolve_invocation(self, n, t: JavaType, m) -> tuple:
        known = self._invocations.get(n.id)
        if known is None:
            known = self._invocations[n.id] = self._resolve_invocation(n, t, m)
        return known

    def _resolve_invocation(self, n, t: JavaType, m) -> tuple:
        name = text(n.child_by_field_name("name"))
        obj = n.child_by_field_name("object")
        args = arguments_of(n)
        if obj is None:
            return self.unqualified(name, args, t, m)
        if obj.type == "super":
            return self.super_call(t, name, args, t, m)
        if any(c.type == "super" for c in n.children):  # `X.super.m()`: the grammar keeps `super` beside the object X
            r = self.resolve_type(text(obj), t, m)
            if r[0] == "repo" and r[1].kind == "interface":  # I.super.m(): the method of the interface I
                return self.select(r[1], name, args, t, m, True, via_super=True)
            if r[0] == "repo":  # C.super.m(): C is t or a class around it, and the call is C's superclass's, not C's own
                enclosing = t
                while enclosing is not None and enclosing is not r[1]:
                    enclosing = enclosing.outer
                if enclosing is None:
                    return ("unres", f"{r[1].fqn}.super does not name a class around the call")
                return self.super_call(r[1], name, args, t, m)
            if r[0] == "ext":
                return ("ext", "super into external interface")
            return ("unres", f"{text(obj)}: {r[1]}")
        r = self.type_of(obj, t, m)
        kind = r[0]
        if kind == "repo":
            return self.select(r[1], name, args, t, m, r[2])
        if kind == "type":
            if isinstance(r[1], JavaType):
                if r[1].dup:
                    return ("unres", f"two files define {r[1].fqn}")
                return self.select(r[1], name, args, t, m, True)
            return ("ext", f"static call on external class {self.label(r[1])}")
        if kind == "ext":
            if self.ext_may_be_repo(r[1], name, len(args)):
                return ("unres", f"'{name}' on {r[1]}: a repo class that may be one declares {name}")
            return ("ext", f"instance of external class {self.label(r[1])}")
        if kind == "prim":
            return ("unres", "call on a primitive")
        if kind == "array":
            return ("ext", "array member")
        if kind == "pkg":
            if r[1].split(".", 1)[0] in self.jdk_roots and "." in r[1]:
                return ("ext", "static call on external class (JDK)")
            return ("unres", f"name '{r[1]}' is not visible")
        return ("unres", r[1])

    def super_call(self, c: JavaType, name: str, args, t: JavaType, m) -> tuple:
        """`super.m()` written in class c (or `C.super.m()` of the class C around t): a call of the superclass's m, which
        is exact (no override of it is reached)."""
        repo_super = [x for x in self.supers_of(c) if isinstance(x, JavaType) and x.kind != "interface"]
        if repo_super:
            return self.select(repo_super[0], name, args, t, m, True, via_super=True)
        outside = [x for x in self.supers_of(c) if not isinstance(x, JavaType)]
        if any(is_class for _, is_class in c.supers if is_class) or outside or name in OBJECT_MEMBERS:
            return ("ext", "super into external base")
        return ("unres", "super")

    def _may_be_instance(self, c: JavaType, ext_name: str) -> bool:
        """Whether an instance of the repo type c may also be of the external type: one of c's supertypes outside the
        repo is it, or may extend it (nothing of it is known unless it is in KNOWN_SUPERTYPES)."""
        simple = ext_name.rsplit(".", 1)[-1]
        for e in self.ancestors(c)[1]:
            if e == OBJECT_EXTERNAL:
                continue
            known = KNOWN_SUPERTYPES.get(e[1]) if e[0] == "ext" else None
            if known is None:
                return True
            for above in (e[1], *known):
                if above == ext_name or (ext_name.startswith("?.") and above.rsplit(".", 1)[-1] == simple):
                    return True
        return False

    def ext_may_be_repo(self, ext_name: str, name: str, n: int) -> bool:
        """Whether a value typed with the external class or interface `ext_name` may be an instance of a repo type that
        declares `name` taking n arguments (`List<String> l = new MyList(); l.add(x)` runs MyList.add), so that the call
        is not provably outside the repo. A final JDK class has no repo subtype; Object has all of them."""
        key = (ext_name, name, n)
        known = self._ext_asked.get(key)
        if known is None:
            # the class that declares the method, or a subtype of it that inherits it: `class Task extends RunBase
            # implements Runnable {}` runs RunBase.run for `Runnable.run()`
            known = self._ext_asked[key] = ext_name not in FINAL_EXTERNAL and any(
                not mm.static
                and not mm.private
                and mm.arity_ok(n)
                and (ext_name == OBJECT or any(self._may_be_instance(x, ext_name) for x in (mm.owner, *self.all_subs(mm.owner))))
                for mm in self.by_name.get(name, ())
            )
        return known

    def implicit_member(self, c: JavaType, name: str, n: int) -> str:
        """Why `name` is a member of c that no source declares, or "": one of Object's (every class has them), an
        enum's own (name, ordinal, values, ...) or the accessor of a record component. These are never repo code."""
        if name in OBJECT_MEMBERS:
            return OBJECT_MEMBER
        kinds = {a.kind for a in self.ancestors(c)[0]}
        if "enum" in kinds and name in ENUM_MEMBERS:
            return IMPLICIT_ENUM_MEMBER
        if "record" in kinds and n == 0 and any(component == name for a in self.ancestors(c)[0] for _, component in a.components):
            return IMPLICIT_RECORD_ACCESSOR
        return ""

    def may_be_imported(self, name: str, fi) -> bool:
        """Whether a static import of the file may supply a method `name`: a single import of it, a static on-demand
        import of a type outside the repo (its members are not known), or of a repo type that has one."""
        if name in fi.static_single:
            return True
        for o in fi.static_ondemand:
            rt = self.repo_type_by_qualified(o)
            if rt is None or self.declares(rt, name, static_only=True):
                return True
        return False

    def unqualified(self, name: str, args, t: JavaType, m) -> tuple:
        c = t
        while c is not None:  # the comb rule
            if self.declares(c, name):
                return self.select(c, name, args, t, m, False)
            implicit = self.implicit_member(c, name, len(args))
            if implicit:  # a member every such class has, so the search ends here and not at an outer class's
                if self.subs_declare(c, name):
                    return ("unres", f"'{name}' is not declared on {c.fqn}, but a subtype of it declares {name}")
                return ("ext", implicit)
            external = [e for e in self.ancestors(c)[1] if e != OBJECT_EXTERNAL and self.may_declare(e, name, len(args))]
            if external:
                outer_has, x = False, c.outer
                while x is not None:
                    outer_has = outer_has or self.declares(x, name)
                    x = x.outer
                first = external[0]
                if outer_has or self.may_be_imported(name, t.finfo):
                    return ("unres", f"'{name}' may be inherited from external base {first[1]} {self.label(first[1])} of {c.fqn}, or be the enclosing class's or an import's")
                if any(e[0] == "unres" for e in external):
                    return ("unres", f"'{name}': a supertype of {c.fqn} could not be resolved")
                if self.subs_declare(c, name):
                    return ("unres", f"'{name}' is inherited from an external supertype of {c.fqn}, but a subtype of it declares {name}")
                return ("ext", "inherited from an external supertype")
            c = c.outer
        fi = t.finfo
        if name in fi.static_single:
            owners = list(dict.fromkeys(fi.static_single[name]))
            if len(owners) > 1:  # the overloads of every owner compete, and an owner outside the repo has its own
                return ("unres", f"'{name}' is imported from {len(owners)} types: {', '.join(owners)}")
            rt = self.repo_type_by_qualified(owners[0])
            if rt is not None:
                return self.select(rt, name, args, t, m, True, static_only=True)
            return ("ext", f"static import {self.label(owners[0])}")
        found, external_on_demand = [], False
        for o in fi.static_ondemand:
            rt = self.repo_type_by_qualified(o)
            if rt is None:
                external_on_demand = True
            elif self.declares(rt, name, static_only=True):
                found.append(rt)
        if len(found) == 1 and not external_on_demand:
            return self.select(found[0], name, args, t, m, True, static_only=True)
        if found:
            return ("unres", f"'{name}' may come from several static on-demand imports")
        if external_on_demand:
            return ("ext", "static on-demand import (external)")
        return ("unres", f"name '{name}' is not defined anywhere ripple can see")

    # ---- recording ----------------------------------------------------------------------------------------------

    def record(self, outcome: tuple, caller: str, at, shown: str, t: JavaType) -> None:
        """Write what a call site came to: edges (an `edge` outcome is [(callee, kind)...]), an external call, or
        an unresolved one."""
        kind, info = outcome
        if kind == "edge":
            line = line_of(at)
            self.edges.extend(Edge(caller, callee, t.file, line, edge_kind) for callee, edge_kind in info)
        elif kind == "ext":
            self.external[info] += 1
        else:
            self.unresolved.append(Unresolved(caller, t.file, line_of(at), shown, info))

    def call(self, n, t: JavaType, m, caller: str) -> None:
        result = self.resolve_invocation(n, t, m)
        name_node = n.child_by_field_name("name")
        name = text(name_node)
        if result[0] == "unres" and not self.lombok:
            # No repo source declares this name with this many arguments: it cannot run repo code.
            k = len(arguments_of(n))
            if not any(mm.arity_ok(k) for mm in self.by_name.get(name, ())) and not result[1].startswith((f"'{name}' is not declared on", "no method")):
                result = ("ext", NO_REPO_METHOD)
        obj = n.child_by_field_name("object")
        self.record(result, caller, name_node, f"{_collapse(text(obj), 60)}.{name}" if obj is not None else name, t)

    def _ctor_accessible(self, c: JavaMethod, rt: JavaType, t: JavaType, protected_ok: bool) -> bool:
        """Whether code in t may call constructor c of rt (JLS 6.6.2.2): a private one only inside its top-level class,
        a package-private one in its package, a protected one in its package or through `super(...)` or an anonymous class."""
        if c.access == "public":
            return True
        if c.access == "private":
            return self._same_top(rt, t)
        return rt.pkg == t.pkg or (c.access == "protected" and protected_ok)

    def constructor(self, rt: JavaType, args, at, shown: str, t: JavaType, m, caller: str, protected_ok: bool = False) -> None:
        """A call of a constructor of the repo type rt: `new`, `this(...)`, `super(...)`, an enum constant. A
        constructor is chosen by arity, then by exact argument types, as a method is, among those t may call."""
        n = len(args)
        if rt.dup:
            return self.record(("unres", f"two files define {rt.fqn}"), caller, at, shown, t)
        if rt.kind == "interface":
            return self.record(("unres", f"{rt.fqn} is an interface, which has no constructor"), caller, at, shown, t)
        constructors = self.constructors_of(rt)
        if any(c.broken for c in constructors):
            return self.record(("unres", f"a constructor of {rt.fqn} could not be read: the parser met an error in its header"), caller, at, shown, t)
        candidates = [c for c in constructors if c.arity_ok(n) and self._ctor_accessible(c, rt, t, protected_ok)]
        if not constructors:
            if n == 0:  # no constructor is written: the class itself runs its initializers
                return self.record(("edge", [(rt.fqn, "constructor")]), caller, at, shown, t)
            return self.record(("unres", "no constructor"), caller, at, shown, t)
        chosen = candidates[0] if len(candidates) == 1 else self.exact(candidates, args, t, m) if candidates else None
        if chosen is not None:
            return self.record(("edge", [(chosen.id, "constructor")]), caller, at, shown, t)
        self.record(("unres", f"{len(candidates)} constructors of {rt.fqn} take {n} arguments"), caller, at, shown, t)

    def constructors_of(self, rt: JavaType) -> list[JavaMethod]:
        """The constructors of rt: those written, and for a record the canonical one that is implicit when none written
        takes the components (JLS 8.10.4), which stays a candidate beside the other constructors it may have. Its id is the
        record's own: the symbol of a class with no constructor written."""
        if rt.kind != "record":
            return rt.ctors
        known = self._implicit.get(rt)
        if known is None:
            components = tuple(self._signature_item(self.resolve_type(w, rt, None)) for w, _ in rt.components)
            canonical = any(
                c.node.type == "compact_constructor_declaration" or tuple(self._signature_item(self.resolve_type(w, rt, c)) for w, _ in c.params) == components
                for c in rt.ctors
            )
            access = {word for word in rt.mods if word in ("public", "protected", "private")}  # the record's own access
            implicit = [] if canonical else [JavaMethod(rt.fqn, CONSTRUCTOR, rt, None, rt.components, False, "", access, set())]
            known = self._implicit[rt] = [*rt.ctors, *implicit]
        return known

    def new(self, n, t: JavaType, m, caller: str) -> None:
        tn = n.child_by_field_name("type")
        args = arguments_of(n)
        shown = strip_type(text(tn))[0]
        first = named(n)[0] if named(n) else None
        if first is not None and first.type not in TYPE_ASKS and first != tn:
            return self.record(("unres", "qualified inner-class creation (outer.new Inner())"), caller, tn, shown, t)
        r = self.resolve_type(text(tn), t, m)
        # `new T(...) { ... }` makes the anonymous class, whose initializers and constructor run whatever T is
        anonymous = self.d.anonymous_at.get(n.id)
        made = [(anonymous.fqn, "constructor")] if anonymous is not None and not anonymous.dup else None
        if r[0] == "repo":
            if r[1].dup:
                return self.record(("unres", f"two files define {r[1].fqn}"), caller, tn, shown, t)
            if made is not None and r[1].kind == "interface":
                return self.record(("edge", made), caller, tn, shown, t)
            self.constructor(r[1], args, tn, shown, t, m, caller, protected_ok=anonymous is not None)
            if made is not None:
                self.record(("edge", made), caller, tn, shown, t)
            return
        if r[0] == "ext":
            if made is not None:
                return self.record(("edge", made), caller, tn, shown, t)
            return self.record(("ext", f"new of external class {self.label(r[1])}"), caller, tn, shown, t)
        self.record(("unres", f"new: {r[1]}"), caller, tn, shown, t)

    def explicit_constructor(self, n, t: JavaType, m, caller: str) -> None:
        args = arguments_of(n)
        word = text(n.child_by_field_name("constructor"))
        if n.child_by_field_name("object") is not None:
            return self.record(("unres", "qualified super(...) of an inner class"), caller, n, word, t)
        if word == "this":
            return self.constructor(t, args, n, "this", t, m, caller)
        repo_super = [x for x in self.supers_of(t) if isinstance(x, JavaType) and x.kind != "interface"]
        if repo_super:
            return self.constructor(repo_super[0], args, n, "super", t, m, caller, protected_ok=True)
        if any(is_class for _, is_class in t.supers if is_class) or t.kind == "enum":
            return self.record(("ext", "super(...) into external base"), caller, n, "super", t)
        self.record(("ext", "super(...) into java.lang.Object"), caller, n, "super", t)

    # ---- the walk -----------------------------------------------------------------------------------------------

    def scan(self, node, t: JavaType, m, caller: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            kind = n.type
            if kind in TYPE_DECLS or kind == "class_body":
                continue  # the members of a local or anonymous class are scanned as symbols of their own
            if kind == "method_invocation":
                self.call(n, t, m, caller)
            elif kind == "object_creation_expression":
                self.new(n, t, m, caller)
            elif kind == "explicit_constructor_invocation":
                self.explicit_constructor(n, t, m, caller)
            stack += n.named_children

    def run(self) -> JavaResolver:
        for t in self.d.every_type:
            self.supers_of(t)
        for t in self.d.every_type:
            for sup in self.supers_of(t):
                if isinstance(sup, JavaType):
                    sup.subs.append(t)
        self.ready = True
        for t in self.d.every_type:
            if not t.dup:
                self._scan_type(t)
        return self

    def _scan_type(self, t: JavaType) -> None:
        for found in [*t.methods.values(), t.ctors]:
            for mm in found:
                body = mm.node.child_by_field_name("body")
                if body is not None:
                    self.scan(body, t, mm, mm.id)
        body = next((c for c in t.node.named_children if c.type == "class_body"), None) if t.kind in ("anon", "enumconst") else t.node.child_by_field_name("body")
        if body is None:
            return
        items = named(body)
        for c in list(items):
            if c.type == "enum_body_declarations":
                items += named(c)
        for c in items:  # field initializers, initializer blocks and enum constants run as the class
            if c.type in ("field_declaration", "static_initializer", "block", "constant_declaration"):
                self.scan(c, t, None, t.fqn)
            elif c.type == "enum_constant":
                arguments = c.child_by_field_name("arguments")
                args = arguments_of(c)
                if arguments is not None:
                    self.scan(arguments, t, None, t.fqn)
                self.constructor(t, args, c, text(c.child_by_field_name("name")), t, None, t.fqn)
