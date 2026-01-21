from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import SubClass
from portal import loggedin_required

# Create the Blueprint for Subclass logic
subclass_bp = Blueprint('subclass', __name__)

@subclass_bp.route('/admin/add_subclass', methods=['GET', 'POST'])
@loggedin_required()
def add_subclass():
    """Route to add a new subclass entry to the MySQL database."""
    if request.method == 'POST':
        try:
            # Capture data from the form
            new_sub = SubClass(
                product_group=request.form.get('product_group', '').strip().upper(),
                subclass_code=request.form.get('subclass_code', '').strip().zfill(3),
                subclass_name=request.form.get('description', '').strip().upper()
            )
            
            db.session.add(new_sub)
            db.session.commit()
            
            flash("Subclass Added Successfully", "success")
            # Redirect back to management focusing on the subclass tab
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
            
    return render_template('add_subclass.html')

@subclass_bp.route('/admin/edit_subclass/<group>/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_subclass(group, code):
    """Route to update an existing subclass using group and code as the unique identifier."""
    # Find the specific subclass using composite key logic
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first_or_404()
    
    if request.method == 'POST':
        try:
            # Update fields from the form
            sub.product_group = request.form.get('product_group').strip().upper()
            sub.subclass_code = request.form.get('subclass_code').strip().zfill(3)
            sub.subclass_name = request.form.get('description').strip().upper()
            
            db.session.commit()
            flash("Subclass Updated Successfully", "success")
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
            
    return render_template('edit_subclass.html', s=sub, old_group=group, old_code=code)

@subclass_bp.route('/admin/delete_subclass/<group>/<code>', methods=['POST'])
@loggedin_required()
def delete_subclass(group, code):
    """Route to remove a subclass using group and code context."""
    # Find the record using both group and code to ensure precision
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first()
    
    if sub:
        try:
            db.session.delete(sub)
            db.session.commit()
            flash(f"Subclass {code} deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Delete Error: {e}", "danger")
    else:
        flash("Subclass not found.", "warning")
        
    return redirect(url_for('admin_management', _anchor='subclass'))