from pygit.objects import hash_object, read_object

data = b"Hello World\n"

sha1 = hash_object(data, "blob")

print("SHA-1:", sha1)

obj_type, content = read_object(sha1)

print("Type:", obj_type)
print("Content:", content)
