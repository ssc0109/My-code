from dolfin import *

from fenapack import PCDKrylovSolver
from fenapack import PCDAssembler
from fenapack import PCDNewtonSolver, PCDNonlinearProblem
from fenapack import StabilizationParameterSD

import argparse, sys, os
#from mpi4py import MPI

parser = argparse.ArgumentParser(description=__doc__, formatter_class=
                                 argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--pcd", type=str, dest="pcd_variant", default="BRM1",
                    choices=["BRM1", "BRM2"], help="PCD variant")
args = parser.parse_args(sys.argv[1:])


#comm = MPI.COMM_WORLD
#rank = comm.Get_rank()
#root = 0



nu = 0.01#Constant(args.viscosity)



#parameters["form_compiler"]["quadrature_degree"] = 3
mr = 10
mesh = BoxMesh(Point(0,0,0), Point(1,1,1), mr,mr,mr)
print (mesh.num_vertices())

eps = 1e-6
class Gamma0(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary
class Gamma1(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and x[0]<eps


boundary_markers = MeshFunction("size_t", mesh, mesh.topology().dim()-1)
boundary_markers.set_all(3)        # interior facets
Gamma0().mark(boundary_markers, 0) # no-slip facets
Gamma1().mark(boundary_markers, 1) # inlet facets01


# Inlet velocity
u_in = Expression(("1.0","0.0","0.0"),degree=1)



# Function Space
V = VectorFunctionSpace(mesh, "CG", 2)
Q = FunctionSpace(mesh, "CG", 1)
V_ele = VectorElement('CG', mesh.ufl_cell(), 2)
Q_ele = FiniteElement('CG', mesh.ufl_cell(), 1)
W_ele = V_ele*Q_ele
W = FunctionSpace(mesh, W_ele)


# Apply bc
bc0 = DirichletBC(W.sub(0), (0.0, 0.0, 0.0), boundary_markers, 0)
bc1 = DirichletBC(W.sub(0), u_in1, boundary_markers, 1)


# Artificial BC for PCD preconditioner
if args.pcd_variant == "BRM1":
    bc_pcd1 = DirichletBC(W.sub(1), 0.0, boundary_markers, 1)
elif args.pcd_variant == "BRM2":
    bc_pcd1 = DirichletBC(W.sub(1), 0.0, boundary_markers, 3)
    bc_pcd2 = DirichletBC(W.sub(1), 0.0, boundary_markers, 4)

u, p = TrialFunctions(W)
v, q = TestFunctions(W)
w = Function(W)
u_ = Constant((1.0,0.0,0.0))

vnorm = sqrt(dot(u_,u_))
h = CellDiameter(mesh)
a = (
      nu*inner(grad(u), grad(v))
    + inner(dot(grad(u), u_), v)
    - p*div(v)
    - q*div(u)
)*dx
tau_supg = ( (2.0*vnorm/h)**2 + 9*(4.0*nu/h**2)**2 )**(-0.5)
tau_pspg = h**2/2#tau_supg#
res = grad(u)*u_+grad(p)-div(nu*grad(u))

#a += tau_supg*inner(grad(v)*u_,res)*dx
#a += -tau_pspg*inner(grad(q),res)*dx

f = Constant((0.0,0.0,0.0))
L = inner(f,v)*dx



#mu = alpha(gamma)*inner(u, v)*dx
mp = Constant(1.0/nu)*p*q*dx
kp = Constant(1.0/nu)*(dot(grad(p), u_))*q*dx
ap = inner(grad(p), grad(q))*dx

if args.pcd_variant == "BRM2":
    n = FacetNormal(mesh)
    ds = Measure("ds", subdomain_data=boundary_markers)
    # TODO: What about the reaction term? Does it appear here?
    kp -= Constant(1.0/nu)*dot(u_, n)*p*q*ds(1)+Constant(1.0/nu)*dot(u_, n)*p*q*ds(2)
    #kp -= Constant(1.0/nu)*dot(u_, n)*p*q*ds(0)  # TODO: Is this beneficial?

pcd_assembler = PCDAssembler(a, L, [bc0, bc1],
                             ap=ap, kp=kp, mp=mp, bcs_pcd=[bc_pcd])
problem = PCDNonlinearProblem(pcd_assembler)

linear_solver = PCDKrylovSolver(comm=mesh.mpi_comm())
linear_solver.parameters["relative_tolerance"] = 1e-6
PETScOptions.set("ksp_monitor")
PETScOptions.set("ksp_gmres_restart", 150)


# Set up subsolvers
PETScOptions.set("fieldsplit_p_pc_python_type", "fenapack.PCDPC_" + args.pcd_variant)


PETScOptions.set("fieldsplit_u_ksp_type", "richardson")
PETScOptions.set("fieldsplit_u_ksp_max_it", 1)
PETScOptions.set("fieldsplit_u_pc_type", "hypre")
PETScOptions.set("fieldsplit_u_pc_hypre_type", "boomeramg")
PETScOptions.set("fieldsplit_p_PCD_Ap_ksp_type", "richardson")
PETScOptions.set("fieldsplit_p_PCD_Ap_ksp_max_it", 2)
PETScOptions.set("fieldsplit_p_PCD_Ap_pc_type", "hypre")
PETScOptions.set("fieldsplit_p_PCD_Ap_pc_hypre_type", "boomeramg")
PETScOptions.set("fieldsplit_p_PCD_Mp_ksp_type", "chebyshev")
PETScOptions.set("fieldsplit_p_PCD_Mp_ksp_max_it", 5)
PETScOptions.set("fieldsplit_p_PCD_Mp_ksp_chebyshev_eigenvalues", "0.5, 2.5")
PETScOptions.set("fieldsplit_p_PCD_Mp_pc_type", "jacobi")

# Apply options
linear_solver.set_from_options()

# Set up nonlinear solver
solver = PCDNewtonSolver(linear_solver)
solver.parameters["relative_tolerance"] = 1e-6

# Solve problem
solver.solve(problem, w.vector())
